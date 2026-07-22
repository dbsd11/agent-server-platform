from logger import logger
import asyncio
_global_loop_ = asyncio.get_event_loop()
import threading
global_loop_thread = threading.Thread(target=lambda:_global_loop_.run_forever(), daemon=True)
global_loop_thread.start()

_work_loops_ = []
import os
for i in range(max(os.cpu_count()-1, 1)):
    def run_work_loop():
        work_loop = asyncio.new_event_loop()
        _work_loops_.append(work_loop)
        try:
            work_loop.run_forever()
        except Exception as e:
            logger.error(f"工作循环 {i} 发生异常: {e}")
        logger.info(f"工作循环 {i} 结束")

    threading.Thread(target=run_work_loop, daemon=True).start()

def get_global_loop():
    return _global_loop_

def get_random_work_loop():
    if not hasattr(get_random_work_loop, '_idx'):
        get_random_work_loop._idx = 0
    idx = get_random_work_loop._idx
    get_random_work_loop._idx = (idx + 1) % len(_work_loops_)
    return _work_loops_[idx]
