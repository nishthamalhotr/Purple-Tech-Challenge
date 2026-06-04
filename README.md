# Store Intelligence System

## Overview

Store Intelligence System is an AI-powered retail analytics platform built for the **Purplle Tech Challenge 2026**. The system processes raw CCTV footage to generate actionable business insights through computer vision, real-time event streaming, analytics, anomaly detection, and REST APIs.

The solution transforms traditional surveillance cameras into intelligent sensors capable of monitoring customer behavior, store activity, occupancy patterns, and operational anomalies in real time.

---

## Problem Statement

Retail stores generate massive amounts of CCTV footage daily, but most of this data remains unused. The goal of this project is to build an end-to-end system that can:

* Detect and track people from CCTV feeds
* Generate real-time store events
* Analyze customer movement patterns
* Detect unusual activities and anomalies
* Provide APIs for querying insights
* Visualize metrics through a live dashboard

---

## Key Features

### Person Detection

* Real-time person detection from CCTV video streams
* Bounding box generation
* Confidence score filtering

### Multi-Object Tracking

* Persistent customer IDs across frames
* Entry and exit monitoring
* Movement trajectory tracking

### Event Generation

* Customer entered store
* Customer exited store
* Zone entered
* Zone exited
* Dwell time exceeded
* Crowd formation
* Anomaly detected

### Real-Time Analytics

* Current occupancy
* Footfall count
* Peak hours
* Average dwell time
* Zone-wise engagement
* Customer movement heatmaps

### Anomaly Detection

* Unusual crowding
* Restricted area access
* Long dwell duration
* Sudden occupancy spikes
* Suspicious movement patterns

### REST APIs

* Occupancy analytics
* Event retrieval
* Historical insights
* Anomaly reports
* Health monitoring

### Live Dashboard

* Real-time metrics
* Occupancy monitoring
* Event stream visualization
* Anomaly alerts
* Store performance insights

---

## System Architecture

```text
CCTV Video Feed
        │
        ▼
Frame Extraction Service
        │
        ▼
Detection Model (YOLO)
        │
        ▼
Tracking Engine (ByteTrack/DeepSORT)
        │
        ▼
Event Processor
        │
        ▼
Kafka Event Stream
        │
        ▼
Analytics Engine
        │
 ┌──────┴──────┐
 ▼             ▼
Database    Anomaly Engine
 │             │
 └──────┬──────┘
        ▼
FastAPI Backend
        │
        ▼
Dashboard / APIs
```

---

## Tech Stack

### Computer Vision

* Python
* OpenCV
* YOLOv8
* ByteTrack / DeepSORT

### Backend

* FastAPI
* Uvicorn

### Event Streaming

* Apache Kafka

### Database

* PostgreSQL
* Redis

### Analytics

* Pandas
* NumPy

### Dashboard

* React.js
* Chart.js

### Deployment

* Docker
* Docker Compose

---

## Event Schema

### Customer Entry Event

```json
{
  "event_id": "evt_001",
  "event_type": "customer_entered",
  "person_id": "person_23",
  "timestamp": "2026-06-01T10:20:30Z",
  "camera_id": "cam_01",
  "confidence": 0.95
}
```

### Zone Entry Event

```json
{
  "event_id": "evt_002",
  "event_type": "zone_entered",
  "person_id": "person_23",
  "zone": "Cosmetics",
  "timestamp": "2026-06-01T10:21:45Z"
}
```

---

## API Endpoints

### Health Check

```http
GET /health
```

### Current Occupancy

```http
GET /analytics/occupancy
```

### Footfall Analytics

```http
GET /analytics/footfall
```

### Event History

```http
GET /events
```

### Anomaly Reports

```http
GET /anomalies
```

---

## Project Structure

```text
store-intelligence-system/
│
├── app/
│   ├── api/
│   ├── core/
│   ├── models/
│   ├── services/
│   └── utils/
│
├── cv_pipeline/
│   ├── detector/
│   ├── tracker/
│   └── event_generator/
│
├── analytics/
│
├── dashboard/
│
├── kafka/
│
├── tests/
│
├── docker-compose.yml
├── requirements.txt
└── README.md
```

---

## Setup Instructions

### Clone Repository

```bash
git clone <repository-url>
cd store-intelligence-system
```

### Create Virtual Environment

```bash
python -m venv .venv
```

### Activate Environment

Windows:

```bash
.venv\Scripts\activate
```

Linux/Mac:

```bash
source .venv/bin/activate
```

### Install Dependencies

```bash
pip install -r requirements.txt
```

### Run Backend

```bash
uvicorn app.main:app --reload
```

### Run Using Docker

```bash
docker-compose up --build
```

---

## Assumptions

* CCTV cameras provide stable video feeds.
* Camera calibration is not required.
* Person tracking IDs remain unique during a session.
* Events are processed in near real time.
* Store zones are predefined.

---

## Challenges & Trade-offs

### Accuracy vs Speed

YOLOv8 Nano provides faster inference while maintaining acceptable detection accuracy.

### Real-Time Processing

Kafka is used to decouple video processing from analytics workloads.

### Scalability

Microservice architecture allows independent scaling of detection, tracking, analytics, and API services.

### Fault Tolerance

Event streaming ensures temporary service failures do not result in data loss.

---

## Future Improvements

* Multi-camera identity re-identification
* Customer demographics estimation
* Product interaction tracking
* Queue length monitoring
* Inventory correlation analytics
* Cloud deployment on AWS/GCP
* Edge AI optimization

---

## Evaluation Metrics

* Detection Accuracy
* Tracking Accuracy
* Event Generation Latency
* API Response Time
* System Throughput
* Anomaly Detection Precision

---

## Author

Submitted for **Purplle Tech Challenge 2026 - Round 2**

Built with a focus on scalability, reliability, and production-grade engineering practices.
