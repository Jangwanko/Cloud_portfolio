from portfolio.config import settings


def ingress_partition_queue(room_id: int) -> str:
    partition = room_id % settings.ingress_partitions
    return f"{settings.ingress_queue}:p{partition}"


def ingress_partition_queues() -> list[str]:
    return [f"{settings.ingress_queue}:p{i}" for i in range(settings.ingress_partitions)]
