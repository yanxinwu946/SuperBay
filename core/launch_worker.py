"""Standalone console worker. Runs independently so closing the UI doesn't kill tools."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import queue
import threading


def execute(request_path):
    request_path=Path(request_path)
    request=json.loads(request_path.read_text(encoding='utf-8'))
    status_path=request_path.with_suffix('.status.json')
    record={k:request[k] for k in ('id','name','path','started','command')}
    def update(state, **extra):
        record.update(state=state, updated=time.time(), **extra)
        temp=status_path.with_suffix('.tmp')
        temp.write_text(json.dumps(record,ensure_ascii=False),encoding='utf-8')
        temp.replace(status_path)
    process=None
    try:
        env=dict(os.environ)
        for key,value in request.get('environment',{}).items():
            if value is None: env.pop(key,None)
            else: env[key]=value
        update('starting')
        print('SuperBay: ' + request['name'])
        print('Live output is also available in SuperBay run history.')
        print('Log: ' + str(request_path.with_suffix('.log')), flush=True)
        with request_path.with_suffix('.log').open('wb') as log:
            argv = request['argv']
            if os.name == 'nt' and argv[0].lower() == 'cmd.exe':
                # cmd consumes shell syntax, not the C-runtime backslash-quote convention.
                argv = 'cmd.exe /d /s /c "' + argv[-1] + '"'
            process=subprocess.Popen(argv,cwd=request['cwd'],env=env,
                                     stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
            update('running',pid=process.pid)
            screen = queue.Queue(maxsize=256)
            done = threading.Event()
            def console_output():
                try:
                    while True:
                        chunk = screen.get()
                        if chunk is None: break
                        sys.stdout.buffer.write(chunk)
                        sys.stdout.buffer.flush()
                except (OSError, AttributeError):
                    pass
                finally:
                    done.set()
            threading.Thread(target=console_output,daemon=True).start()
            while True:
                chunk = process.stdout.read1(4096)
                if not chunk: break
                log.write(chunk);log.flush()
                try: screen.put_nowait(chunk)
                except queue.Full: pass  # Console selection must not block log capture.
            process.stdout.close()
            code=process.wait()
            try: screen.put_nowait(None)
            except queue.Full: pass
            done.wait(.2)
        update('finished' if code==0 else 'failed',exit_code=code)
    except Exception as error:
        update('failed',error=str(error))
    finally:
        request_path.unlink(missing_ok=True)

if __name__=='__main__': execute(sys.argv[1])
