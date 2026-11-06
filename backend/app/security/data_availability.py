from starlette.responses import JSONResponse

from app.services.data_startup import available


class DataAvailabilityMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (scope['type'] == 'http' and not available()
                and scope['path'] != '/health' and not scope['path'].startswith('/api/data/')):
            await JSONResponse({'detail': {'code': 'data_unavailable'}}, status_code=503)(scope, receive, send)
            return
        await self.app(scope, receive, send)
