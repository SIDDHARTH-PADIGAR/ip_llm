# overIP - Intelligent Patent Analysis Platform

## What It Does
overIP (IP LLM) is a comprehensive patent analysis and analytics engine designed to augment patent attorneys, engineers, and IP researchers. By deeply integrating with the European Patent Office (EPO) Open Patent Services (OPS) API, it automates the extraction and structuring of intricate patent data, including claims, prior art citations, and prosecution history.

It leverages Large Language Models (LLMs) to perform:
- **Prior Art Correlation**: Intelligently mapping claims against cited prior art documents to identify exact limitations and high-risk overlaps.
- **Prosecution History Estoppel Analysis**: Processing legal events, rejections, and applicant amendments over the history of a patent to surface statements that narrow claim scope.
- **Claim Evolution Tracking**: Following variations of a claim over time.
- **Automated Defensive Reporting**: Generating rich, token-cited HTML & PDF defensive or offensive analysis reports based on structured findings.

## Why It Was Built
Patent analysis is heavily bottlenecked by manual log-parsing, reading thousands of pages of obscure legal history ("file wrappers"), and manually tracking down which limitations were disclosed in which prior art. This project was built to programmatically retrieve these artifacts, structure them into query-able graph features, and apply modern reasoning engines (LLMs) to generate deterministic, legally-grounded intelligence. It aims to cut down a multiday patent evaluation workflow into a few minutes.

## Tech Stack
**Frontend:**
- **[Streamlit](https://streamlit.io/)**: For rapidly building the interactive data visualization, event timelines, and document rendering interfaces.

**Backend:**
- **[FastAPI](https://fastapi.tiangolo.com/)**: A high-performance async API for handing internal routes, proxying requests, and handling authentication.
- **SQLite + JWT (Jose)**: Lightweight, self-contained relational storage (`dev.db`) combined with JSON Web Tokens for stateless user sessions.

**AI & Data:**
- **[OpenRouter](https://openrouter.ai/)**: For routing to state-of-the-art LLMs. Used for structured extraction, summarization, and domain-specific patent reasoning.
- **EPO OPS API**: The primary data integration for world patent data (bibliographic, claims, legal events, family).
- **OCR/Extraction**: `pytesseract` and `pdf2image` for augmenting parsed XML/HTML claims with visual document processing.
- **Data parsing**: `xmltodict`, `pydantic` for schema enforcement.

## Architecture Overview
The system follows a decoupled monolithic architecture with distinct ingestion, analysis, and presentation layers:

1. **API / Auth Layer (`src/api/`)**: `fast_api_app.py` serves as the backend entry point. It manages JWT-based user authentication, connects to a local SQLite database (`dev.db`), and exposes REST endpoints consumed by the Streamlit frontend.
2. **Ingestion & Fetching (`src/ops_fetcher.py`, `src/api/epo_client.py`)**: Responsible for negotiating OAuth tokens with the EPO and retrieving raw XML data covering bibliographic details, claims, and file histories. Has caching mechanisms (`prior_art_cache.json`) to minimize costly downstream calls.
3. **Data Parsing & Harmonization (`src/data/`, `src/ops_extractor.py`)**: Transforms raw XML/JSON representations of patents into normalized internal schemas representing Claims, Legal Events, and Published Families.
4. **Analysis Engines (`src/prior_art_correlator.py`, `src/prosecution_history_estoppel.py`)**: The core logic layer. Correlates event sequences, passes structured data along with custom prompts (`report_prompt.py`) to the LLM backend (via OpenRouter), and evaluates confidence scopes (e.g., scoring citations as "examiner", "legal", or "applicant").
5. **Presentation & Reporting (`src/app.py`, `src/reporting.py`, `src/visualization.py`)**: `app.py` is the main Streamlit application. It provides an intuitive GUI to input publication numbers, visualize interactive timelines of legal events, and dispatch report generation tasks. `reporting.py` coordinates generating final deliverables (HTML/PDF) adhering to strict strict citation guardrails (`report_guardrails.py`).

## Example Output Report

When overIP generates a report, it yields a deterministic HTML document with clickable source tokens linking back to the EPO OPS JSON response. Here is an example of what an exported report looks like for patent `EP3000000`:

> ### Coverage: events=4, citations=1
>
> ### Executive Summary
> - Patent rights lapsed on 2018-06-12 due to non-payment; restoration procedures may be available within statutory deadlines—assess commercial viability immediately. `[EVT#4]`
> - No post-grant opposition was filed; eliminates near-term invalidation risk and strengthens claim validity for enforcement and licensing. `[EVT#2]`
> - Reference EP14735465A presents obviousness concerns; commission detailed claim mapping and prepare technical response to defend independent claims. `[CIT#1]`
> - Limited family scope; evaluate national extension strategy in high-value markets and assess filing gaps. `[EVT#1]`
>
> ### Timeline Analysis
> - **2016-12-09 [INTG]**: Intention to grant announced; independent claims are allowable, strengthening litigation position and enabling licensing conversations. `[EVT#2]`
> - **2018-02-20 [26N]**: No opposition filed; claim scope unlikely to be challenged in post-grant proceedings, reducing invalidation exposure. `[EVT#3]`
> - **2018-06-12 [GBPC]**: Patent ceased in GB due to non-payment; enforcement rights suspended unless restoration is filed; assess statutory deadlines and commercial justification. `[EVT#4]`
>
> ### Prior Art Analysis
> Reference EP14735465A may support obviousness rejection if combined with secondary art; prepare expert technical declaration distinguishing inventive steps and non-obvious combinations. `*(Ranked #1)*` `[CIT#1]`

## How to Run locally

### Prerequisites
- Python 3.10+
- Valid API keys for EPO OPS and OpenRouter.

### 1. Setup Environment
Clone the repository and spin up a virtual environment:

```bash
cd ip_llm_v3
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure Settings
Ensure you have a `.env` file at the root of the project with your secrets:
```env
# Example .env config
SECRET_KEY="your_jwt_secret_here"
API_BASE="http://localhost:8000/api"

# EPO OPS Credentials
EPO_CONSUMER_KEY="your_epo_consumer_key"
EPO_CONSUMER_SECRET="your_epo_consumer_secret"

# OpenRouter Configuration
OPENROUTER_API_KEY="your_openrouter_api_key"
```

### 3. Start the Backend API
In one terminal instance, launch the FastAPI server:
```bash
cd src
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

### 4. Start the Frontend
In another terminal instance, start the Streamlit application:
```bash
cd src
streamlit run app.py
```

The application will be accessible at `http://localhost:8501`.

## Key Design Decisions
- **Deterministic Token Guardrails**: LLMs hallucinate, and in patent law, hallucinations are disastrous. The codebase natively embeds strict tokenization mechanisms (`[CIT#k]`, `[EVT#k]`). Before any report fragment makes it to the user, `report_guardrails.py` verifies all generated assertions trace back to exact ground-truth tokens derived from the EPO database. Missing citations result in sentences being scrubbed entirely (`drop_uncited_sentences`).
- **Synchronous but Decoupled Analysis UI**: While Streamlit limits pure async event-loops, the app aggressively caches expensive LLM inferences & API responses (`prior_art_cache.json` and `@st.cache_data`) preventing redundant calls while switching tabs or expanding analysis details.
- **FastAPI for State**: By offloading Authentication and State to a FastAPI+SQLite backend rather than using Streamlit's ephemeral state hacks, the application can easily be refactored down the line into a fully fledged React/Next.js client without needing to rewrite the actual app boundary logic.
- **Prioritization Scoring on Events**: The `prosecution_history_estoppel.py` and front-end rendering engines inherently weigh legal events. E.g. Intentions to Grant ("INTG") and Oppositions are treated with high gravity, while minor bureaucratic lapses are assigned lower weight. This enables concise summarizing for the end-user rather than flooding them with raw docket noise.
