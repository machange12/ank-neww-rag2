import streamlit as st
import requests
import json

st.set_page_config(page_title="LawFirm RAG — Test UI", layout="wide")
st.title("LawFirm RAG — Test UI")

col1, col2 = st.columns([2, 3])

with col1:
    st.header("Connection")
    api_url = st.text_input("API base URL", value="http://localhost:8000")
    endpoint = st.selectbox("Endpoint", ["/lawfirm-chat-trigger-006", "/lawfirm-chat-stream"])
    token = st.text_input("Authorization token (Bearer ...)", type="password")

    st.header("Chat Input")
    chat_input = st.text_area("chatInput", value="Summarise the latest NDA.")
    session_id = st.text_input("sessionId", value="test-session-1")

    if st.button("Send (non-streaming)"):
        url = api_url.rstrip("/") + endpoint
        headers = {"Content-Type": "application/json"}
        if token:
            if token.lower().startswith("bearer "):
                headers["Authorization"] = token
            else:
                headers["Authorization"] = f"Bearer {token}"
        payload = {"chatInput": chat_input, "sessionId": session_id}
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            st.write("Status:", resp.status_code)
            try:
                st.json(resp.json())
            except Exception:
                st.text(resp.text)
        except Exception as exc:
            st.error(f"Request failed: {exc}")

with col2:
    st.header("Quick Actions")
    st.write("Use these to inspect server endpoints or list Drive files when the server is running.")
    api_root = api_url.rstrip("/")

    if st.button("GET /healthz"):
        try:
            r = requests.get(api_root + "/healthz", timeout=10)
            st.write(r.status_code)
            st.json(r.json())
        except Exception as e:
            st.error(e)

    if st.button("List Drive files (/documents/drive-files)"):
        try:
            headers = {}
            if token:
                headers["Authorization"] = token if token.lower().startswith("bearer ") else f"Bearer {token}"
            r = requests.get(api_root + "/documents/drive-files", headers=headers, timeout=30)
            st.write(r.status_code)
            try:
                st.json(r.json())
            except Exception:
                st.text(r.text)
        except Exception as e:
            st.error(e)

st.markdown("---")
st.markdown("Instructions:\\n\\n1) Activate your venv: `.venv\\Scripts\\activate` on Windows.\\n2) Start the FastAPI server: `uvicorn app:app --reload --port 8000`.\\n3) In another terminal run: `streamlit run streamlit_app.py`.\\n4) Paste your Authorization token (or use the UI to provide it) and send a test chat.")
