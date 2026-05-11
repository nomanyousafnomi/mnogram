# MNOGRAM — Complete Setup, Deployment & Demo Guide

---

## 1. Run Locally (no Azure needed)

```bash
# Clone / place files in a folder
cd mnogram/

# Create virtual environment
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run
streamlit run app.py
```

Open http://localhost:8501 in your browser.

**Demo credentials:**

| Username  | Password    | Role     |
|-----------|-------------|----------|
| admin     | admin123    | Admin    |
| creator   | creator123  | Creator  |
| user      | user123     | Consumer |

Without Azure credentials the app runs fully in **local SQLite mode** — all features work, Azure logos show "Local Mode".

---

## 2. Azure Services Setup

### 2a. Azure Storage (Blob)

1. Go to https://portal.azure.com → **Create a resource** → **Storage account**
2. Name: `mnostorage` (must be globally unique)
3. Region: East US (or nearest)
4. Redundancy: LRS (cheapest for dev)
5. Click **Review + Create**
6. After creation: **Access keys** → copy **Connection string**
7. Create container: **Containers** → **+ Container** → Name: `mnogram-media` → Public access: Blob

Paste into `app.py`:
```python
AZURE_STORAGE_CONNECTION_STRING = "DefaultEndpointsProtocol=https;AccountName=..."
```

---

### 2b. Azure Cosmos DB

1. **Create a resource** → **Azure Cosmos DB** → **Core (SQL)**
2. Account name: `mno-cosmos`
3. Region: same as storage
4. Capacity: **Serverless** (free-tier friendly)
5. After creation: **Keys** → copy **URI** and **PRIMARY KEY**
6. **Data Explorer** → **New Database**: `mnogram-db`
7. **New Container**: `media-items` → Partition key: `/uploader`

Paste into `app.py`:
```python
AZURE_COSMOS_URI = "https://mno-cosmos.documents.azure.com:443/"
AZURE_COSMOS_KEY = "your-primary-key-here=="
```

---

### 2c. Azure Cognitive Services (Computer Vision)

1. **Create a resource** → **Computer Vision** (under AI + Machine Learning)
2. Name: `mno-vision`
3. Pricing tier: **F0** (free — 5000 calls/month)
4. After creation: **Keys and Endpoint**
5. Copy **Key 1** and **Endpoint**

Paste into `app.py`:
```python
AZURE_COGNITIVE_KEY      = "your-key-here"
AZURE_COGNITIVE_ENDPOINT = "https://mno-vision.cognitiveservices.azure.com/"
```

---

## 3. Deploy to Azure App Service (Docker)

### 3a. Build and push Docker image

```bash
# Login to Azure
az login

# Create resource group
az group create --name mnogram-rg --location eastus

# Create Azure Container Registry
az acr create --resource-group mnogram-rg --name mnoacr --sku Basic

# Build and push image
az acr build --registry mnoacr --image mnogram:latest .
```

### 3b. Deploy to Azure App Service

```bash
# Create App Service Plan (B2 = 2 vCPU, 3.5 GB RAM)
az appservice plan create \
  --name mnogram-plan \
  --resource-group mnogram-rg \
  --is-linux \
  --sku B2

# Create Web App from container
az webapp create \
  --resource-group mnogram-rg \
  --plan mnogram-plan \
  --name mnogram-app \
  --deployment-container-image-name mnoacr.azurecr.io/mnogram:latest

# Set environment variables (Azure credentials)
az webapp config appsettings set \
  --resource-group mnogram-rg \
  --name mnogram-app \
  --settings \
    AZURE_STORAGE_CONNECTION_STRING="your-connection-string" \
    AZURE_COSMOS_URI="your-cosmos-uri" \
    AZURE_COSMOS_KEY="your-cosmos-key" \
    AZURE_COGNITIVE_KEY="your-cognitive-key" \
    AZURE_COGNITIVE_ENDPOINT="your-endpoint" \
    WEBSITES_PORT=8501

# Enable container pull from ACR
az webapp identity assign --resource-group mnogram-rg --name mnogram-app
az acr update --name mnoacr --admin-enabled true
```

App will be live at: `https://mnogram-app.azurewebsites.net`

---

## 4. Deploy to Azure Container Apps (recommended for scaling demo)

```bash
# Create Container Apps environment
az containerapp env create \
  --name mnogram-env \
  --resource-group mnogram-rg \
  --location eastus

# Deploy container app with autoscaling
az containerapp create \
  --name mnogram \
  --resource-group mnogram-rg \
  --environment mnogram-env \
  --image mnoacr.azurecr.io/mnogram:latest \
  --target-port 8501 \
  --ingress external \
  --min-replicas 2 \
  --max-replicas 20 \
  --cpu 1.0 \
  --memory 2.0Gi \
  --scale-rule-name http-rule \
  --scale-rule-type http \
  --scale-rule-http-concurrency 100 \
  --env-vars \
    AZURE_STORAGE_CONNECTION_STRING="your-string" \
    AZURE_COSMOS_URI="your-uri" \
    AZURE_COSMOS_KEY="your-key" \
    AZURE_COGNITIVE_KEY="your-key" \
    AZURE_COGNITIVE_ENDPOINT="your-endpoint"
```

Autoscaling triggers at 100 concurrent HTTP requests per replica.

---

## 5. Demo Walkthrough (for video recording)

**Suggested order:**

1. **Login as admin** (admin/admin123)
   - Show the **Admin Dashboard** — live metrics updating every 3s
   - Point out: Active Users, Latency gauge, Node Count, traffic spike alerts
   - Show the **Node Grid** expanding/contracting

2. **Scaling Metrics page**
   - Show HPA graph: nodes auto-scale with traffic
   - CDN cache hit % by region
   - Upload queue + worker status table
   - Load balancer distribution bar chart

3. **System Logs page**
   - Live log stream with trace IDs
   - Show filtering by level / service
   - Export logs to JSON

4. **Sign out → Login as creator** (creator/creator123)
   - Upload an image with title + caption + AI tagging
   - Show AI analysis: tags, generated caption, moderation result
   - Upload confirmation with Blob URL

5. **Sign out → Login as user** (user/user123)
   - Browse feed — see creator's post
   - Like and comment
   - Use search: type a tag word and see filtered results

6. **Architecture page** (any user)
   - Show pipeline diagram
   - Design principles tab
   - Capacity planning table

---

## 6. Scalability Explanation (for presentation)

**Why Mnogram is cloud-native:**

| Concern          | Solution                                   |
|------------------|--------------------------------------------|
| Media storage    | Azure Blob — unlimited, 11 9s durability   |
| Metadata/queries | Cosmos DB — global, <10ms p99              |
| AI processing    | Cognitive Services — managed, pay-per-call |
| Caching          | Redis + CDN — reduces origin load 85%      |
| Compute scaling  | Container Apps HPA — 2 to 20 replicas      |
| Queue processing | Service Bus — decouple upload from AI step |
| Global routing   | Azure Front Door — nearest edge PoP        |
| Observability    | Azure Monitor + App Insights               |

**Stateless = horizontally scalable.** Every replica is identical; no sticky sessions; load balancer can send any request to any node.

---

## 7. Limitations & Future Improvements

| Limitation                            | Production Fix                              |
|---------------------------------------|---------------------------------------------|
| SQLite local fallback                 | Always use Cosmos DB in prod                |
| Metrics are simulated                 | Wire to Azure Monitor / Prometheus          |
| Single Streamlit process              | Migrate UI to React + FastAPI backend       |
| No video transcoding                  | Add Azure Media Services pipeline           |
| No real-time push                     | Add Azure SignalR for live feed             |
| Auth is hardcoded                     | Use Azure AD B2C / Entra ID                 |
| No multi-region failover              | Enable Cosmos DB multi-region writes        |
| CDN config is conceptual              | Configure Azure CDN rules engine            |

---

## 8. File Structure

```
mnogram/
├── app.py                  ← Single-file application
├── requirements.txt        ← Python dependencies
├── Dockerfile              ← Container definition
├── streamlit_config.toml   ← Streamlit theme settings
├── SETUP.md                ← This file
└── mnogram_local.db        ← Auto-created SQLite DB (local mode)
```
