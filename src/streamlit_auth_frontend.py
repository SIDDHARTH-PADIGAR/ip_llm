# src/streamlit_auth_frontend.py
import streamlit as st
import requests
import os

API_BASE = os.getenv("API_BASE", "http://localhost:8000/api")

st.set_page_config(page_title="Auth Demo", layout="centered")
st.title("Auth Demo — Sign Up / Log In")

if "token" not in st.session_state:
    st.session_state["token"] = None
if "user" not in st.session_state:
    st.session_state["user"] = None

col1, col2 = st.columns(2)

with col1:
    st.header("Sign Up")
    su_email = st.text_input("Email (signup)", key="su_email")
    su_password = st.text_input("Password (signup)", type="password", key="su_password")
    su_name = st.text_input("Name (optional)", key="su_name")
    if st.button("Sign Up"):
        payload = {"email": su_email, "password": su_password, "name": su_name}
        try:
            r = requests.post(f"{API_BASE}/signup", json=payload, timeout=10)
            if r.status_code == 201:
                st.success("Signup successful — you can now log in.")
            else:
                st.error(f"Signup failed: {r.status_code} {r.text}")
        except Exception as e:
            st.error(f"Error contacting backend: {e}")

with col2:
    st.header("Log In")
    li_email = st.text_input("Email (login)", key="li_email")
    li_password = st.text_input("Password (login)", type="password", key="li_password")
    if st.button("Log In"):
        payload = {"email": li_email, "password": li_password}
        try:
            r = requests.post(f"{API_BASE}/login", json=payload, timeout=10)
            if r.status_code == 200:
                token = r.json().get("access_token")
                st.session_state["token"] = token
                st.success("Logged in successfully.")
                headers = {"Authorization": f"Bearer {token}"}
                me = requests.get(f"{API_BASE}/me", headers=headers, timeout=10)
                if me.status_code == 200:
                    st.session_state["user"] = me.json()
            else:
                st.error(f"Login failed: {r.status_code} {r.text}")
        except Exception as e:
            st.error(f"Error contacting backend: {e}")

st.markdown("---")
if st.session_state.get("token"):
    st.write(f"Logged in as: **{st.session_state.get('user', {}).get('email', 'unknown')}**")
    st.json(st.session_state.get("user", {}))
    if st.button("Log out"):
        st.session_state["token"] = None
        st.session_state["user"] = None
        st.success("Logged out.")
else:
    st.info("Not logged in. Use the form above.")