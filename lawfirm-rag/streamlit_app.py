import requests
import streamlit as st


st.set_page_config(page_title="LawFirm RAG — Test UI", layout="wide")
st.title("LawFirm RAG — Test UI")


def bearer(token: str) -> str:
    return token if token.lower().startswith("bearer ") else f"Bearer {token}"


def auth_headers() -> dict[str, str]:
    return {"Authorization": bearer(st.session_state["token"])}


def login(api_root: str) -> None:
    st.header("Login")
    email = st.text_input("Email")
    password = st.text_input("Password", type="password")
    if st.button("Login"):
        try:
            r = requests.post(f"{api_root}/auth/login", json={"email": email, "password": password})
            if r.status_code == 200:
                data = r.json()
                st.session_state["token"] = data["access_token"]
                st.session_state["access_level"] = data.get("access_level", 1)
                st.session_state["email"] = email
                st.rerun()
            elif r.status_code == 401:
                st.error("Invalid email or password.")
            elif r.status_code == 403:
                st.error("Your account does not have access. Contact your administrator.")
            else:
                st.error(f"Login failed. Please try again. (Error {r.status_code})")
        except requests.exceptions.ConnectionError:
            st.error("Cannot reach the server. Make sure the backend is running on port 8000.")


def render_upload(api_root: str) -> None:
    if st.session_state.get("access_level", 1) < 3:
        st.info("Upload is available for access level 3 and above.")
        return

    st.subheader("Upload Document")
    uploaded_file = st.file_uploader("Choose a file", type=["pdf", "docx", "txt"], key="doc_uploader")
    matter_id = st.text_input("Matter ID (optional)", key="matter_id_input")
    access_lvl = st.selectbox("Access Level", [1, 2, 3, 4, 5], key="access_lvl_select")

    if uploaded_file and st.button("Upload & Ingest"):
        with st.spinner("Ingesting document..."):
            files = {"file": (uploaded_file.name, uploaded_file.getvalue(), uploaded_file.type)}
            params = {"matter_id": matter_id, "access_level": access_lvl}
            r = requests.post(f"{api_root}/documents/upload", files=files, params=params, headers=auth_headers())
            if r.status_code == 200:
                data = r.json()
                st.success(f"Ingested {data['chunks']} chunks")
            else:
                st.error(f"Upload failed: {r.text}")


def render_recent_chats(api_root: str) -> None:
    st.subheader("Recent Chats")
    try:
        sess_r = requests.get(f"{api_root}/sessions", headers=auth_headers(), timeout=20)
    except requests.exceptions.ConnectionError:
        st.caption("Could not load recent chats.")
        return

    if sess_r.status_code == 200:
        sessions = sess_r.json().get("sessions", [])
        if not sessions:
            st.caption("No previous chats yet.")
        for session in sessions:
            label = session["title"] or "Untitled"
            if st.button(label[:40], key=session["session_id"]):
                st.session_state["active_session"] = session["session_id"]
                st.rerun()
    else:
        st.caption("Could not load recent chats.")


def render_documents(api_root: str) -> None:
    st.subheader("Ingested Documents")
    r = requests.get(f"{api_root}/documents", headers=auth_headers())
    if r.status_code == 200:
        docs = r.json().get("documents", [])
        if not docs:
            st.info("No documents have been ingested yet. Upload a document using the left panel.")
            return
        for doc in docs:
            col1, col2, col3 = st.columns([3, 1, 1])
            with col1:
                if doc.get("url"):
                    st.markdown(f"[{doc['file_title']}]({doc['url']})")
                else:
                    st.write(doc["file_title"])
            with col2:
                st.caption(doc.get("matter_id") or "—")
            with col3:
                st.caption((doc.get("ingested_at") or "")[:10])
    else:
        st.error("Could not load documents.")


def render_admin(api_root: str) -> None:
    st.subheader("User Management")
    headers = auth_headers()
    r = requests.get(f"{api_root}/admin/users", headers=headers)
    if r.status_code == 200:
        for user in r.json().get("users", []):
            st.write(f"**{user['email']}** — Access Level {user['access_level']}")
    else:
        st.error("Could not load users.")

    st.markdown("---")
    st.subheader("Add New User")
    new_email = st.text_input("Email", key="admin_new_email")
    new_password = st.text_input("Password", type="password", key="admin_new_password")
    new_level = st.selectbox("Access Level", [1, 2, 3, 4, 5], key="admin_new_level")
    if st.button("Create User"):
        r = requests.post(
            f"{api_root}/admin/users",
            json={"email": new_email, "password": new_password, "access_level": new_level},
            headers=headers,
        )
        if r.status_code == 200:
            st.success(f"Created user {new_email}")
        else:
            st.error(f"Failed: {r.text}")


col1, col2 = st.columns([2, 3])

with col1:
    st.header("Connection")
    api_url = st.text_input("API base URL", value="http://localhost:8000")
    api_root = api_url.rstrip("/")

    if not st.session_state.get("token"):
        login(api_root)
    else:
        st.success(f"Signed in as {st.session_state.get('email', 'user')}")
        st.caption(f"Access level: {st.session_state.get('access_level', 1)}")
        if st.button("Logout"):
            st.session_state.clear()
            st.rerun()

        st.markdown("---")
        endpoint = st.selectbox("Endpoint", ["/lawfirm-chat-trigger-006", "/lawfirm-chat-stream"])

        st.header("Chat Input")
        chat_input = st.text_area("chatInput", value="Summarise the latest NDA.")
        session_id = st.text_input("sessionId", value=st.session_state.get("active_session", "test-session-1"))

        if st.button("Send (non-streaming)"):
            try:
                r = requests.post(
                    api_root + endpoint,
                    headers={"Content-Type": "application/json", **auth_headers()},
                    json={"chatInput": chat_input, "chat_input": chat_input, "sessionId": session_id},
                    timeout=60,
                )
                st.write("Status:", r.status_code)
                if r.status_code == 200:
                    response = r.json()
                    answer = response.get("answer") or response.get("output") or ""
                    if not answer.strip():
                        st.warning("No relevant documents found. Please upload documents related to your query.")
                    else:
                        st.markdown(answer)
                    if response.get("sources"):
                        st.markdown("**Sources:**")
                        for src in response["sources"]:
                            st.markdown(f"- [{src['title']}]({src['url']})")
                    else:
                        st.info("No source documents were returned for this answer.")
                    with st.expander("Raw response"):
                        st.json(response)
                elif r.status_code == 401:
                    st.error("Session expired. Please log in again.")
                    st.session_state.clear()
                    st.rerun()
                else:
                    st.error(f"Something went wrong ({r.status_code}). Please try again.")
                    st.text(r.text)
            except requests.exceptions.Timeout:
                st.error("The request timed out. Try a more specific question.")
            except requests.exceptions.ConnectionError:
                st.error("Lost connection to the server.")

        st.markdown("---")
        render_upload(api_root)
        st.markdown("---")
        render_recent_chats(api_root)

with col2:
    if st.session_state.get("token"):
        if st.session_state.get("access_level", 1) == 5:
            tab_actions, tab_docs, tab_admin = st.tabs(["Quick Actions", "Documents", "Admin"])
        else:
            tab_actions, tab_docs = st.tabs(["Quick Actions", "Documents"])
            tab_admin = None

        with tab_actions:
            st.header("Quick Actions")
            st.write("Use these to inspect server endpoints or list Drive files when the server is running.")

            if st.button("GET /healthz"):
                try:
                    r = requests.get(api_root + "/healthz", timeout=10)
                    st.write(r.status_code)
                    st.json(r.json())
                except Exception as exc:
                    st.error(exc)

            if st.button("List Drive files (/documents/drive-files)"):
                try:
                    r = requests.get(api_root + "/documents/drive-files", headers=auth_headers(), timeout=30)
                    st.write(r.status_code)
                    try:
                        st.json(r.json())
                    except Exception:
                        st.text(r.text)
                except Exception as exc:
                    st.error(exc)

        with tab_docs:
            render_documents(api_root)

        if tab_admin is not None:
            with tab_admin:
                render_admin(api_root)
    else:
        st.header("Quick Actions")
        st.info("Log in on the left to use chat, upload, documents, and admin tools.")

st.markdown("---")
st.markdown("Instructions:\\n\\n1) Backend: `uvicorn app:app --reload --port 8000`.\\n2) Frontend: `streamlit run streamlit_app.py --server.port 8501`.\\n3) Log in, then upload appears for access level 3+ users.")
