# mnogram

Mnogram is a **Streamlit media distribution web app** (Instagram-like) designed for coursework demonstration of **scalable software engineering concepts**.

## Features

- Role-based app views:
  - **Creator view**: upload photos and set metadata (title, caption, location, people present).
  - **Consumer view**: search/filter feed, view photos, comment, and rate (1-5).
- Persistent data layer using SQLite for:
  - photos + metadata
  - comments
  - ratings
- Scalability-oriented implementation patterns:
  - query/result caching via `st.cache_data`
  - indexed data access
  - paginated feed retrieval
  - role separation and storage abstraction
- In-app **Scalability Evidence Dashboard** with data volume counters.

## Run locally

1. Create and activate a virtual environment (recommended).
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Start the app:

```bash
streamlit run app.py
```

4. Open the URL shown by Streamlit (usually `http://localhost:8501`).

## Demo accounts

- Usernames are fixed: `creator1`, `creator2`, `viewer1`, `viewer2`.
- Passwords are read from Streamlit secrets (`CREATOR1_PASSWORD`, `CREATOR2_PASSWORD`, `CONSUMER1_PASSWORD`, `CONSUMER2_PASSWORD`).
- If secrets are not set, secure random fallback passwords are generated at runtime and shown in the app’s **Demo accounts** expander.

## Rubric alignment notes (distinction-focused evidence)

- **Problem definition**: app models creator-vs-consumer personas and content distribution workflow.
- **Technical solution**: layered structure (UI, service logic, persistence, media storage) and deployable Streamlit app.
- **Advanced features**: role-based access model, caching, data indexing/pagination, interaction analytics.
- **Scalability assessment**: in-app metrics and explicit patterns that can be migrated to managed cloud services.
- **Limitations to discuss in slides**:
  - single-node SQLite for demo only (swap for managed DB in production)
  - local file storage (swap for cloud object storage such as S3/Azure Blob)
  - simple username/password demo auth (swap for cloud IAM/Cognito/Auth0)

## Suggested cloud deployment pathway

- Containerize app with Docker.
- Deploy on cloud compute service (e.g., Azure App Service, AWS ECS/Fargate, OpenStack VM).
- Externalize DB/storage to managed services.
- Place CDN and edge caching in front of static media.
- Add CI/CD pipeline (GitHub Actions) for automated test/build/deploy.
