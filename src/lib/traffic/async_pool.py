import asyncio


class Pool:
    def __init__(self, size):
        self.size = size
        self._sem = asyncio.Semaphore(value=size)
        self._tasks = set()

    async def add(self, coroutine):
        await self._sem.acquire()

        task = asyncio.create_task(coroutine)
        task.add_done_callback(self._task_done_callback)
        self._tasks.add(task)

        return task

    async def join(self):
        await asyncio.gather(*self._tasks)

    def _task_done_callback(self, task):
        if task in self._tasks:
            self._tasks.remove(task)
            self._sem.release()
