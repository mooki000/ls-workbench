"""hsap.py as pilot.py starts it (python3 -I hsap_wrap.py <hsap's own arguments>), with the launcher replaced by the fake: the real hsap code, the real steps, a fake game."""
import importlib.util
import os
import sys

real, fake = os.environ['REHEARSE_HSAP'], os.environ['REHEARSE_HS_RUN']
spec = importlib.util.spec_from_file_location('hsap', real)
mod = importlib.util.module_from_spec(spec)
sys.modules['hsap'] = mod
spec.loader.exec_module(mod)
mod.HS_RUN = fake
sys.exit(mod.main())
