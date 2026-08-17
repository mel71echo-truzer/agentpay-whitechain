import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


app = FastAPI(title="AgentPay Registry")

REGISTRY_FILE = Path(__file__).parent / "services.json"


class ServiceRegistration(BaseModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    url: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    method: str = Field(default="POST")
    price_units: int = Field(gt=0)
    currency: str = Field(default="apUSD")
    network: str = Field(default="whitechain-testnet")
    category: str = Field(min_length=1)

    # Reputation / quality
    rating: float = Field(default=5.0, ge=0, le=5)
    success_rate: float = Field(default=1.0, ge=0, le=1)
    latency_ms: int = Field(default=500, gt=0)


def load_services():
    if not REGISTRY_FILE.exists():
        return []

    with open(REGISTRY_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    return data.get("services", [])


def save_services(services):
    with open(REGISTRY_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {"services": services},
            f,
            indent=2,
            ensure_ascii=False
        )


@app.get("/")
def root():
    services = load_services()

    return {
        "service": "AgentPay Registry",
        "status": "online",
        "services": len(services)
    }


@app.get("/services")
def get_services(
    category: str | None = None,
    max_price: int | None = None
):
    services = load_services()

    if category:
        services = [
            service
            for service in services
            if service["category"].lower() == category.lower()
        ]

    if max_price is not None:
        services = [
            service
            for service in services
            if int(service["price_units"]) <= max_price
        ]

    return {
        "count": len(services),
        "services": services
    }


@app.get("/services/{name}")
def get_service(name: str):
    services = load_services()

    for service in services:
        if service["name"].lower() == name.lower():
            return service

    raise HTTPException(
        status_code=404,
        detail="Service not found"
    )


@app.post("/services/register")
def register_service(service: ServiceRegistration):
    services = load_services()

    for existing in services:
        if existing["name"].lower() == service.name.lower():
            raise HTTPException(
                status_code=409,
                detail="Service already registered"
            )

    new_service = service.model_dump()

    services.append(new_service)
    save_services(services)

    return {
        "success": True,
        "message": "Service registered",
        "service": new_service
    }


@app.delete("/services/{name}")
def delete_service(name: str):
    services = load_services()

    filtered = [
        service
        for service in services
        if service["name"].lower() != name.lower()
    ]

    if len(filtered) == len(services):
        raise HTTPException(
            status_code=404,
            detail="Service not found"
        )

    save_services(filtered)

    return {
        "success": True,
        "message": "Service removed",
        "name": name
    }