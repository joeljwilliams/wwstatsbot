"""@wwstatsbot — the application package.

Deliberately empty of code. The release job in ci.yml reads the version with
``python3 -c 'import wwstatsbot.version …'`` on a runner that has installed no
dependencies at all, so anything imported here — or in any sub-package's
``__init__`` — would turn that into a ModuleNotFoundError at exactly the moment a
release is being cut. Sub-packages are imported by their own names
(``from wwstatsbot.data import db``); nothing is re-exported from here.
"""
