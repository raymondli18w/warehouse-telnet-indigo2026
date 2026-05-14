import streamlit as st
import pandas as pd
import time
import threading
import queue
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime
from pathlib import Path
import sys
import re
from io import BytesIO
import subprocess

# Page configuration
st.set_page_config(
    page_title="Warehouse Telnet Processor",
    page_icon="📦",
    layout="wide"
)

# ============= TELNET WORKER CLASS =============
class TelnetProcessor:
    def __init__(self, df_valid, df_invalid, batch_id, status_queue):
        self.df_valid = df_valid.copy()
        self.df_invalid = df_invalid.copy()
        self.batch_id = batch_id
        self.status_queue = status_queue
        
        # Configuration
        self.HOST = "18whe.camelot3plcloud.com"
        self.PORT = 6667
        self.USERNAME = "bhunt"
        self.PASSWORD = "123"
        self.MAIN_MENU_1 = "1"
        self.SUB_MENU_7 = "7"
        self.DELAY_SHORT = 0.5
        self.DELAY_LONG = 2.0
        self.PAUSE_BETWEEN_CASES = 1
        
        # Status tracking
        self.status = {
            'total_count': len(df_valid) + len(df_invalid),
            'valid_count': len(df_valid),
            'invalid_count': len(df_invalid),
            'processed_count': 0,
            'success_count': 0,
            'error_count': 0,
            'current_case': '',
            'completed': False,
            'recent_logs': [],
            'filename': '',
            'batch_id': batch_id,
            'email_sent': False
        }
    
    def add_log(self, message):
        timestamp = datetime.now().strftime('%H:%M:%S')
        log_entry = f"[{timestamp}] {message}"
        self.status['recent_logs'].append(log_entry)
        if len(self.status['recent_logs']) > 50:
            self.status['recent_logs'] = self.status['recent_logs'][-50:]
        self.status_queue.put({'type': 'log', 'message': log_entry})
        print(log_entry)
    
    def connect_and_login(self):
        """Establish telnet connection using subprocess (more reliable)"""
        self.add_log(f"Connecting to {self.HOST}:{self.PORT}...")
        
        try:
            # Use subprocess with telnet
            import telnetlib3
            import asyncio
            
            # For now, let's simulate a successful connection for testing
            # We'll add real telnet once we debug
            self.add_log("⚠️ Telnet simulation mode - Replace with actual connection")
            return "simulated"
            
        except Exception as e:
            self.add_log(f"Telnet error: {str(e)}")
            return None
    
    def scan_case_simulation(self, case_id, location_id):
        """Simulate scanning for testing"""
        self.add_log(f"📝 SIMULATION: Scanning {case_id} -> {location_id}")
        # Simulate 90% success rate
        import random
        return "Success" if random.random() > 0.1 else "Error"
    
    def process(self):
        try:
            self.add_log("=" * 50)
            self.add_log("STARTING PROCESSING (SIMULATION MODE)")
            self.add_log(f"Valid cases: {self.status['valid_count']}")
            self.add_log("=" * 50)
            
            if not self.df_valid.empty:
                if 'Result' not in self.df_valid.columns:
                    self.df_valid['Result'] = ""
                
                for index, row in self.df_valid.iterrows():
                    case_id = str(row['CaseID']).zfill(20)
                    location_id = str(row['LocationID']).strip().upper()
                    
                    self.status['current_case'] = f"CaseID: {case_id}, Location: {location_id}"
                    self.add_log(f"Processing {index+1}/{self.status['valid_count']}: {case_id}")
                    self.status_queue.put({'type': 'status', 'status': self.status})
                    
                    # Use simulation for now
                    result = self.scan_case_simulation(case_id, location_id)
                    self.df_valid.at[index, 'Result'] = result
                    
                    if result == "Success":
                        self.status['success_count'] += 1
                        self.add_log(f"✅ Success for {case_id}")
                    else:
                        self.status['error_count'] += 1
                        self.add_log(f"❌ Error for {case_id}")
                    
                    self.status['processed_count'] += 1
                    self.status_queue.put({'type': 'status', 'status': self.status})
                    time.sleep(0.5)  # Small delay for simulation
            
            # Mark invalid cases
            if not self.df_invalid.empty:
                if 'Result' not in self.df_invalid.columns:
                    self.df_invalid['Result'] = "Invalid Format"
            
            self.add_log("=" * 50)
            self.add_log("PROCESSING COMPLETE")
            self.status['completed'] = True
            self.status_queue.put({'type': 'status', 'status': self.status})
            self.status_queue.put({'type': 'complete', 'df_valid': self.df_valid, 'df_invalid': self.df_invalid})
                
        except Exception as e:
            self.add_log(f"FATAL ERROR: {str(e)}")
            self.status['completed'] = True
            self.status_queue.put({'type': 'status', 'status': self.status})

# ============= EMAIL FUNCTION =============
def send_completion_email(status, df_valid, df_invalid, log_content):
    SMTP_CONFIG = {
        "server": "smtp.office365.com",
        "port": 587,
        "username": "donotreply@18wheels.ca",
        "password": "E@B9KCE7Z**ejRq",
        "to_emails": ["Raymond.li@18wheels.ca", "customer.service@18wheels.ca"]
    }
    
    try:
        success_count = len(df_valid[df_valid['Result'] == 'Success']) if not df_valid.empty else 0
        
        subject = f"Processing Complete - {status['filename']}"
        
        body = f"""
Warehouse Processing Summary
============================
File: {status['filename']}
Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

Results:
- Total Cases: {status['total_count']}
- Valid Cases: {status['valid_count']}
- Successfully Processed: {success_count}
- Failed: {status['error_count']}
- Invalid Format: {status['invalid_count']}

This is a SIMULATION. Telnet connection is being debugged.
"""
        
        msg = MIMEMultipart()
        msg['From'] = SMTP_CONFIG["username"]
        msg['To'] = ", ".join(SMTP_CONFIG["to_emails"])
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))
        
        # Create Excel file
        excel_buffer = BytesIO()
        with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
            df_valid.to_excel(writer, sheet_name='Valid_Cases', index=False)
            df_invalid.to_excel(writer, sheet_name='Invalid_Format', index=False)
        
        excel_buffer.seek(0)
        excel_attachment = MIMEBase('application', 'vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        excel_attachment.set_payload(excel_buffer.read())
        encoders.encode_base64(excel_attachment)
        excel_attachment.add_header('Content-Disposition', 'attachment', filename=f'Results_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx')
        msg.attach(excel_attachment)
        
        with smtplib.SMTP(SMTP_CONFIG["server"], SMTP_CONFIG["port"]) as server:
            server.starttls()
            server.login(SMTP_CONFIG["username"], SMTP_CONFIG["password"])
            server.send_message(msg)
        
        return True
    except Exception as e:
        st.error(f"Email error: {str(e)}")
        return False

# ============= VALIDATION FUNCTION =============
def validate_location_id(location_id):
    if pd.isna(location_id) or location_id == '':
        return False
    location_str = str(location_id).strip().upper()
    pattern = r'^M\d{7}$'
    return bool(re.match(pattern, location_str))

# ============= SESSION STATE =============
if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False
if 'processing_thread' not in st.session_state:
    st.session_state.processing_thread = None
if 'status_queue' not in st.session_state:
    st.session_state.status_queue = None
if 'processing_active' not in st.session_state:
    st.session_state.processing_active = False
if 'current_status' not in st.session_state:
    st.session_state.current_status = None
if 'final_df_valid' not in st.session_state:
    st.session_state.final_df_valid = None
if 'final_df_invalid' not in st.session_state:
    st.session_state.final_df_invalid = None

# ============= LOGIN =============
if not st.session_state.authenticated:
    st.title("🔐 Warehouse System Login")
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        password = st.text_input("Enter Access Code", type="password")
        if st.button("Login", use_container_width=True):
            if password.upper() == "IN01":
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("❌ Invalid access code")
    st.stop()

# ============= MAIN APP =============
st.title("📦 Warehouse Telnet Processor")
st.markdown(f"**Logged in:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# Warning about simulation mode
st.warning("⚠️ **SIMULATION MODE ACTIVE** - Telnet connection is being debugged. Currently simulating successful scans for testing.")

with st.sidebar:
    if st.button("Logout", use_container_width=True):
        st.session_state.authenticated = False
        st.rerun()
    st.divider()
    st.markdown("""
    **Instructions:**
    1. Upload Excel (CaseID & LocationID columns)
    2. Click Start Processing
    3. Results will be emailed
    """)

col1, col2 = st.columns([2, 1])

with col1:
    st.subheader("📁 File Upload")
    uploaded_file = st.file_uploader("Choose Excel file (.xlsx)", type=['xlsx'])
    
    if uploaded_file:
        df_raw = pd.read_excel(uploaded_file, dtype=str, keep_default_na=False)
        
        if 'CASEID' in df_raw.columns:
            df_raw.rename(columns={'CASEID': 'CaseID'}, inplace=True)
        if 'LOCATIONID' in df_raw.columns:
            df_raw.rename(columns={'LOCATIONID': 'LocationID'}, inplace=True)
        
        if 'LocationID' in df_raw.columns:
            df_raw['Valid_Format'] = df_raw['LocationID'].apply(validate_location_id)
            df_valid = df_raw[df_raw['Valid_Format'] == True].copy()
            df_invalid = df_raw[df_raw['Valid_Format'] == False].copy()
            df_valid = df_valid.drop(columns=['Valid_Format'])
            df_invalid = df_invalid.drop(columns=['Valid_Format'])
            
            col_v1, col_v2 = st.columns(2)
            col_v1.metric("✅ Valid Cases", len(df_valid))
            col_v2.metric("❌ Invalid Cases", len(df_invalid))
            
            with st.expander("Preview Valid Cases"):
                st.dataframe(df_valid.head(10))
            
            if st.button("🚀 Start Processing", type="primary"):
                if len(df_valid) > 0:
                    batch_id = datetime.now().strftime('%Y%m%d_%H%M%S')
                    st.session_state.status_queue = queue.Queue()
                    st.session_state.processing_active = True
                    st.session_state.final_df_valid = None
                    st.session_state.final_df_invalid = None
                    
                    processor = TelnetProcessor(df_valid, df_invalid, batch_id, st.session_state.status_queue)
                    processor.status['filename'] = uploaded_file.name
                    thread = threading.Thread(target=processor.process, daemon=True)
                    thread.start()
                    st.session_state.processor = processor
                    st.success(f"✅ Processing started!")
                    st.rerun()
        else:
            st.error("Excel file must contain 'LocationID' column")

with col2:
    st.subheader("📊 Status")
    
    # Check for updates
    if st.session_state.status_queue:
        try:
            while True:
                update = st.session_state.status_queue.get_nowait()
                if update['type'] == 'status':
                    st.session_state.current_status = update['status']
                elif update['type'] == 'complete':
                    st.session_state.final_df_valid = update['df_valid']
                    st.session_state.final_df_invalid = update['df_invalid']
        except queue.Empty:
            pass
    
    if st.session_state.current_status:
        status = st.session_state.current_status
        
        st.metric("📊 Total", status.get('total_count', 0))
        st.metric("✅ Processed", status.get('processed_count', 0))
        st.metric("✔️ Success", status.get('success_count', 0))
        st.metric("❌ Errors", status.get('error_count', 0))
        
        if status.get('valid_count', 0) > 0:
            progress = status.get('processed_count', 0) / status.get('valid_count', 0)
            st.progress(progress)
        
        st.subheader("📝 Logs")
        for log in status.get('recent_logs', [])[-10:]:
            if "✅" in log:
                st.success(log)
            elif "❌" in log:
                st.error(log)
            else:
                st.text(log)
        
        # Send email when complete
        if status.get('completed', False) and not status.get('email_sent', False):
            if st.session_state.final_df_valid is not None:
                with st.spinner("📧 Sending email..."):
                    log_content = "\n".join(status.get('recent_logs', []))
                    if send_completion_email(status, st.session_state.final_df_valid, st.session_state.final_df_invalid, log_content):
                        status['email_sent'] = True
                        st.success("✅ Email sent!")
                        st.balloons()
                
                if st.button("Clear"):
                    st.session_state.processing_active = False
                    st.rerun()
    else:
        st.info("Waiting to start...")

# Auto-refresh
if st.session_state.processing_active:
    time.sleep(2)
    st.rerun()
