"""treadle.service: Treadle as a service another program can call.

One set of calls (core.Service), three surfaces over it:

    python -m treadle types|describe|survey|run ...     the command line (cli.py)
    python -m treadle http --pack NAME                  JSON over HTTP, localhost only (http.py)
    python -m treadle mcp --pack NAME                   an MCP server on stdio (mcp.py)

Runs write one event shape whatever the pack (events.py, treadle.run/1); surveys are kept
server-side under an id (surveys.py). Standard library only, like the rest of the engine.
See docs/treadle/SERVICE.md.
"""
from .core import PROTOCOL, Service, describe
from .errors import ServiceError
from .events import PROTOCOL as EVENT_PROTOCOL
from .events import EventLog, to_event
from .runs import RunStore
from .surveys import FORM_PROTOCOL, SurveyStore

__all__ = ["EVENT_PROTOCOL", "FORM_PROTOCOL", "PROTOCOL", "EventLog", "RunStore", "Service",
           "ServiceError", "SurveyStore", "describe", "to_event"]
