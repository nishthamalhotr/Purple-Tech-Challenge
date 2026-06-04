import asyncio
import uuid
from datetime import datetime, timedelta
from httpx import AsyncClient
from app.main import app

async def main():
    ts = datetime.utcnow()
    async with AsyncClient(app=app, base_url='http://test') as client:
        for i in range(15):
            payload = {
                'event_id': str(uuid.uuid4()),
                'store_id': 'store_001',
                'camera_id': 'cam_001',
                'event_type': 'queue_join',
                'timestamp': (ts + timedelta(seconds=i * 10)).isoformat(),
                'track_id': f't{i}',
                'visitor_id': f't{i}',
                'is_staff': False,
                'confidence': 0.9,
            }
            r = await client.post('/events/ingest', json=payload)
            print('posted', i, r.status_code, r.text)
        resp = await client.get('/stores/store_001/anomalies')
        print('anomalies', resp.status_code, resp.text)

asyncio.run(main())
