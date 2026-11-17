from kbapp.workflows.context import AppContext
from kbapp.search.indexing import Search
from kbapp.web.service import create_app

def service_for(store):
    app = create_app(AppContext(store.paths, store, Search(store), False))
    state = app.state
    state.app = app
    state.store = store
    state.search_engine = app.state.context.search
    for route in app.routes:
        if route.name == 'search':
            state.search = route.endpoint
    state.TOKEN = next((r.endpoint() for r in app.routes if r.name == 'session'))['token']
    return state
