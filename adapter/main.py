import asyncio
import json
import logging
import os
import sys
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

_logger = logging.getLogger(__name__)
_logger.setLevel(logging.INFO)

handler = logging.StreamHandler(sys.stderr)  # Логи в поток ошибок
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
handler.setFormatter(formatter)
_logger.addHandler(handler)


KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS")
TOPIC_SERVICE_1 = "service_1_topic"
TOPIC_SERVICE_2 = "service_2_topic"
TOPIC_RESPONSE = "response_topic"


pending_responses = dict()


class RequestModel(BaseModel):
    service: str
    payload: dict


class KafkaAdapter:
    def __init__(self):
        self.producer = None
        self.consumer = None
        self.pending_responses = {}

    async def start(self):
        loop = asyncio.get_running_loop()
        self.producer = AIOKafkaProducer(
            bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS, loop=loop
        )
        await self.producer.start()
        _logger.info("producer has started")
        self.consumer = AIOKafkaConsumer(
            TOPIC_RESPONSE,
            bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
            group_id="adapter_response_group",
            auto_offset_reset="earliest",
            loop=loop,
        )
        await self.consumer.start()
        _logger.info("consumer has started")

        # Запускаем таск для чтения ответов
        asyncio.create_task(self._consume_responses())

    async def stop(self):
        if self.producer:
            await self.producer.stop()
        if self.consumer:
            await self.consumer.stop()

    async def _consume_responses(self):
        async for msg in self.consumer:
            try:
                response = json.loads(msg.value.decode())
                correlation_id = response.get("correlation_id")
                if correlation_id and correlation_id in self.pending_responses:
                    future = self.pending_responses.pop(correlation_id)
                    future.set_result(response)
            except Exception as e:
                print(f"Error processing response: {e}")
                _logger.error(f"Error processing response: {e}")

    async def send_message(self, service: str, payload: dict):
        if service == "service_1":
            topic = TOPIC_SERVICE_1
        elif service == "service_2":
            topic = TOPIC_SERVICE_2
        else:
            raise ValueError("Unknown service")

        correlation_id = str(uuid.uuid4())
        message = {
            "correlation_id": correlation_id,
            "payload": payload,
        }

        future = asyncio.get_running_loop().create_future()
        self.pending_responses[correlation_id] = future

        await self.producer.send_and_wait(topic, json.dumps(message).encode())

        response = await asyncio.wait_for(future, timeout=10.0)
        return response


async def on_startup(app_: FastAPI) -> None:
    await adapter.start()


async def on_shutdown(app_: FastAPI) -> None:
    await adapter.stop()


@asynccontextmanager
async def lifespan(app_: FastAPI) -> AsyncGenerator[None, None]:
    await on_startup(app_)
    _logger.info("Application has started")
    yield
    await on_shutdown(app_)
    _logger.info("Application has stopped")


app = FastAPI(lifespan=lifespan)
adapter = KafkaAdapter()


@app.post("/send/")
async def send_message(request: RequestModel):
    try:
        response = await adapter.send_message(request.service, request.payload)
        response.pop("correlation_id", None)
        return {"status": "response received", "data": response.get("data")}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Response timeout")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Kafka error: {e}")
