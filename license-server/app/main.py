from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="snflwr.ai License Server")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    from app import webhooks, auth, license_api
    app.include_router(webhooks.router)
    app.include_router(auth.router)
    app.include_router(license_api.router)

    return app


app = create_app()
