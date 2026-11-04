"""routes/ — the HTTP surface, split by what each group of endpoints is about.

api.py was 2,361 lines holding 59 routes, their request models, the ownership helpers and
several pages' worth of generated HTML. Nothing there was wrong, but one file is where you
stop being able to see the shape of the surface, and it is the file every endpoint added
since has had to be threaded into.

The split is by SUBJECT, not by HTTP verb: `pages` serves things a person looks at,
`deps` holds what every group needs (who is asking, where the files are). Each module
exposes an `APIRouter` that api.py includes, so the app is still assembled in exactly one
place and route order stays explicit.
"""
