import os
import time
import threading
from watchdog.observers.polling import PollingObserver
from watchdog.events import FileSystemEventHandler

class AutoOrganizeHandler(FileSystemEventHandler):
    def __init__(self, callback_func):
        super().__init__()
        self.callback_func = callback_func
        self.debounce_timer = None

    def on_created(self, event):
        if event.is_directory:
            return

        # 파일이 완전히 복사될 때까지 약간의 지연 시간을 주는 디바운스 처리
        if self.debounce_timer:
            self.debounce_timer.cancel()

        self.debounce_timer = threading.Timer(3.0, self.callback_func)
        self.debounce_timer.start()

def start_folder_watcher(watch_dir: str, trigger_callback):
    """지정된 폴더를 감시하다가 파일이 생성되면 트리거 함수를 실행합니다."""
    if not os.path.exists(watch_dir):
        os.makedirs(watch_dir, exist_ok=True)

    event_handler = AutoOrganizeHandler(trigger_callback)

    # 도커 환경의 볼륨 마운트에서는 PollingObserver가 파일 이벤트를 더 안정적으로 잡아냅니다.
    observer = PollingObserver()
    observer.schedule(event_handler, watch_dir, recursive=False)

    observer_thread = threading.Thread(target=observer.start, daemon=True)
    observer_thread.start()
    return observer