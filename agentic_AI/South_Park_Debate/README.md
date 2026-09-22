# 🎤 South Park: Town Hall Debate (Multi-Agent LLM App)

An interactive, multi-agent Large Language Model (LLM) application built with **Streamlit** and powered by the **Groq API**. Watch as over 25 classic South Park characters engage in absurd, fully automated Lincoln-Douglas style town hall debates, complete with dynamic topic generation, a panel of celebrity judges, and OpenTelemetry instrumentation for real-time performance tracking.

## ✨ Features

- **Multi-Agent Orchestration:** Simulates an autonomous debate using multiple distinct LLM system prompts interacting with each other's outputs.
- **Dynamic Topic Generation:** "Mayor McDaniels" dynamically generates a unique, absurd debate topic for every session.
- **25+ Unique Personas:** Carefully engineered system prompts capture the vocal mannerisms and logical fallacies of classic characters (e.g., Cartman's mental gymnastics, Timmy's limited vocabulary, PC Principal's microaggression policing) without triggering API safety guardrails.
- **Judge Panel & Grand Finale:** Independent agent judges (Chef, Mr. Mackey, Wendy) evaluate the transcript, before passing their scorecards to "Terrance & Phillip" for the final verdict.
- **OpenTelemetry Integration:** Built-in OTEL tracing monitors latency and tracks token economy (Prompt, Completion, and Total Tokens) per agent call to ensure optimal free-tier API usage.
- **Production-Ready Secrets Management:** Uses Streamlit's native `secrets.toml` architecture for secure API key handling.

## 🛠️ Tech Stack

- **Frontend/UI:** [Streamlit](https://streamlit.io/)
- **LLM Provider:** [Groq API](https://groq.com/) (Targeting open-weights models like `gpt-oss-20b` for ultra-low latency generation)
- **Observability:** [OpenTelemetry (OTEL)](https://opentelemetry.io/) SDK & API
- **Language:** Python 3.8+

## 🚀 Getting Started

### 1. Prerequisites

You will need Python installed on your machine and a free API key from [Groq Console](https://console.groq.com/).

### 2. Installation

Clone this repository or download the source code, then install the required dependencies:

```bash
pip install -r requirements.txt
```
