from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests
from datetime import datetime, timedelta
import math
import os
from dotenv import load_dotenv
from typing import List, Optional

load_dotenv()  # load environment variables from .env

app = FastAPI(title="Family Tracker API", description="Exposes family member tracking data from Traccar for use in LLM chat systems.")

# --- Configuration from environment ---
TRACCAR_URL = os.getenv("TRACCAR_URL")
TRACCAR_USER = os.getenv("TRACCAR_USER")
TRACCAR_PASSWORD = os.getenv("TRACCAR_PASSWORD")
try:
    HOME_COORDINATES = (
        float(os.getenv("HOME_LAT")),
        float(os.getenv("HOME_LON")),
    )
except (TypeError, ValueError):
    HOME_COORDINATES = (-37.814, 144.96332)  # fallback default

# --- Auth Helper ---
def traccar_get(path: str):
    try:
        response = requests.get(f"{TRACCAR_URL}{path}", auth=(TRACCAR_USER, TRACCAR_PASSWORD))
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail=f"Traccar API error: {response.text}")
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Connection error: {str(e)}")

# --- Models ---
class Device(BaseModel):
    id: int
    name: str
    uniqueId: str
    status: str
    lastUpdate: Optional[datetime] = None
    category: Optional[str] = None

class Location(BaseModel):
    latitude: float
    longitude: float
    address: str
    speed: float
    deviceId: int
    deviceName: str
    timestamp: datetime
    battery: Optional[int] = None

class DistanceFromHome(BaseModel):
    user: str
    distance_km: float
    home_lat: float
    home_lon: float
    current_lat: float
    current_lon: float

class User(BaseModel):
    id: int
    name: str
    email: str
    administrator: bool = False

# --- Utilities ---
def haversine(lat1, lon1, lat2, lon2):
    R = 6371  # Earth radius in km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

# --- API Endpoints ---

@app.get("/", summary="API Info")
def root():
    return {"message": "Family Tracker API", "version": "1.0.0"}

@app.get("/family", response_model=List[User], summary="List all users")
def list_users():
    """Get all users in the system"""
    return traccar_get("/users")

@app.get("/family/devices", response_model=List[Device], summary="List all devices")
def list_devices():
    """Get all devices in the system"""
    devices_data = traccar_get("/devices")
    devices = []
    for device in devices_data:
        devices.append(Device(
            id=device['id'],
            name=device['name'],
            uniqueId=device['uniqueId'],
            status=device.get('status', 'unknown'),
            lastUpdate=datetime.fromisoformat(device['lastUpdate'].replace('Z', '+00:00')) if device.get('lastUpdate') else None,
            category=device.get('category')
        ))
    return devices

@app.get("/family/{device_id}/location", response_model=Location)
def get_last_location(device_id: int):
    """Get the latest location for a specific device"""
    try:
        # Get all positions and devices
        positions = traccar_get("/positions")
        devices = {d['id']: d['name'] for d in traccar_get("/devices")}
        
        # Find the position for the specific device
        device_position = None
        for pos in positions:
            if pos['deviceId'] == device_id:
                device_position = pos
                break
        
        if not device_position:
            raise HTTPException(status_code=404, detail=f"No location found for device {device_id}")
        
        # Extract battery information
        battery = None
        if 'attributes' in device_position and 'battery' in device_position['attributes']:
            try:
                battery = int(float(device_position['attributes']['battery']))
            except (ValueError, TypeError):
                battery = None
        
        # Extract timestamp
        timestamp = datetime.utcnow()  # Default fallback
        if 'deviceTime' in device_position:
            try:
                timestamp_str = device_position['deviceTime']
                if timestamp_str.endswith('Z'):
                    timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                else:
                    timestamp = datetime.fromisoformat(timestamp_str)
            except ValueError:
                pass
        elif 'fixTime' in device_position:
            try:
                timestamp_str = device_position['fixTime']
                if timestamp_str.endswith('Z'):
                    timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                else:
                    timestamp = datetime.fromisoformat(timestamp_str)
            except ValueError:
                pass
        
        return Location(
            latitude=device_position['latitude'],
            longitude=device_position['longitude'],
            address=device_position.get('address', 'Unknown'),
            speed=device_position.get('speed', 0.0),
            deviceId=device_id,
            deviceName=devices.get(device_id, "Unknown"),
            timestamp=timestamp,
            battery=battery
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving location: {str(e)}")

@app.get("/family/{device_id}/distance-from-home", response_model=DistanceFromHome)
def get_distance_from_home(device_id: int):
    """Calculate distance from home for a specific device"""
    pos = get_last_location(device_id)
    distance = haversine(HOME_COORDINATES[0], HOME_COORDINATES[1], pos.latitude, pos.longitude)
    return DistanceFromHome(
        user=pos.deviceName,
        distance_km=round(distance, 2),
        home_lat=HOME_COORDINATES[0],
        home_lon=HOME_COORDINATES[1],
        current_lat=pos.latitude,
        current_lon=pos.longitude
    )

@app.get("/family/{device_id}/daily-summary")
def get_daily_summary(device_id: int):
    """Get daily summary report for a specific device"""
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = today + timedelta(days=1)
    try:
        report = traccar_get(f"/reports/summary?deviceId={device_id}&from={today.isoformat()}Z&to={tomorrow.isoformat()}Z")
        return report
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving daily summary: {str(e)}")

@app.get("/zones")
def get_geofences():
    """Get all geofences/zones"""
    return traccar_get("/geofences")

@app.get("/events")
def get_recent_events():
    """Get events from the last hour"""
    one_hour_ago = datetime.utcnow() - timedelta(hours=1)
    return traccar_get(f"/events?from={one_hour_ago.isoformat()}Z&to={datetime.utcnow().isoformat()}Z")

@app.get("/health")
def health_check():
    """Health check endpoint"""
    try:
        # Test connection to Traccar
        traccar_get("/session")
        return {"status": "healthy", "traccar": "connected"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}