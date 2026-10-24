#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
import os,pathlib,signal,subprocess,sys,tempfile
signal.pthread_sigmask(signal.SIG_SETMASK,[])
with tempfile.TemporaryDirectory(prefix='arc-sbs-ui-') as temporary:
    root=pathlib.Path(temporary)
    worker=root/'worker';worker.write_text('''#!/usr/bin/python3
import json,pathlib,sys,time
job=json.loads(pathlib.Path(sys.argv[-1]).read_text())
time.sleep(.25)
pathlib.Path(job['output']).write_text('converted')
print(json.dumps({'event':'completed','result':{'output':job['output'],'layout':'full','eye_order':'right_left'}}),flush=True)
''');worker.chmod(0o755)
    player=root/'mpvr';player.write_text('''#!/usr/bin/python3
import json,os,pathlib,sys
(pathlib.Path(os.environ['ARC_SBS_TEST_ROOT'])/'player.json').write_text(json.dumps(sys.argv[1:]))
''');player.chmod(0o755)
    model=root/'model.xml';model.write_text('test model')
    env={**os.environ,'FLTK_BACKEND':'x11','XDG_CURRENT_DESKTOP':'Dome','XDG_CONFIG_HOME':str(root/'config'),'XDG_CACHE_HOME':str(root/'cache'),'XDG_STATE_HOME':str(root/'state'),'ARC_SBS_PYTHON':str(worker),'ARC_SBS_MODEL':str(model),'ARC_SBS_TEST_ROOT':str(root),'PATH':str(root)+os.pathsep+os.environ['PATH'],'LIBGL_ALWAYS_SOFTWARE':'1'}
    env.pop('WAYLAND_DISPLAY',None)
    subprocess.run(['xvfb-run','-a','dbus-run-session','--',sys.argv[1],str(root)],env=env,check=True,timeout=35)
