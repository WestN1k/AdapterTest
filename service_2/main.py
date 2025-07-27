import asyncio
import json
import logging
import os
import sys
from logging import getLogger

from aiohttp import web
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

_logger = getLogger(__name__)
_logger.setLevel(logging.INFO)

handler = logging.StreamHandler(sys.stderr)  # Логи в поток ошибок
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
handler.setFormatter(formatter)
_logger.addHandler(handler)

is_ready = False
is_live = True


KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS")
TOPIC_SERVICE_1 = "service_2_topic"
TOPIC_RESPONSE = "response_topic"
GROUP_ID = "service_2_group"


async def kafka_worker():
    consumer = AIOKafkaConsumer(
        TOPIC_SERVICE_1, bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS, group_id=GROUP_ID
    )
    producer = AIOKafkaProducer(bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS)
    await consumer.start()
    await producer.start()

    global is_ready
    is_ready = True

    try:
        async for msg in consumer:
            data = json.loads(msg.value.decode())
            correlation_id = data.get("correlation_id")
            payload = data.get("payload")
            _logger.info(
                f"Service_2 received payload: {payload} (correlation_id={correlation_id})"
            )

            # Пример обработки
            result = {"result": f"Processed by service_2 - {payload}"}

            response = {"correlation_id": correlation_id, "data": result}
            await producer.send_and_wait(TOPIC_RESPONSE, json.dumps(response).encode())

            _logger.info(f"Service_2 sent response for correlation_id={correlation_id}")

    except Exception as e:
        _logger.error(f"Kafka error in service_2: {e}")
    finally:
        await consumer.stop()
        await producer.stop()
        is_ready = False


async def handle_liveness(request):
    if is_live:
        return web.Response(text="Alive", status=200)
    else:
        return web.Response(text="Not Alive", status=503)


async def handle_readiness(request):
    if is_ready:
        return web.Response(text="Ready", status=200)
    else:
        return web.Response(text="Not Ready", status=503)


async def init_app():
    app = web.Application()
    app.add_routes(
        [
            web.get("/health/live", handle_liveness),
            web.get("/health/ready", handle_readiness),
        ]
    )
    return app


async def main():
    kafka_task = asyncio.create_task(kafka_worker())
    app = await init_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=8080)
    await site.start()
    _logger.info("Service_2 aiohttp server has started on port 8080")
    try:
        await kafka_task
    except asyncio.CancelledError:
        pass
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
