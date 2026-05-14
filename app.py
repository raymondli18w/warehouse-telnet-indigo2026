import streamlit as st
import pandas as pd
import time
import threading
import queue
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime
import json
from pathlib import Path
import pexpect  # Changed from wexpect to pexpect
import sys
import re
from io import BytesIO

# Page configuration
st.set_page_config(
    page_title="Warehouse Telnet Processor",
    page_icon="📦",
    layout="wide"
)

# Custom CSS
st.markdown("""
<style>
    .stButton > button {
        width: 100%;
        background-color: #4CAF50;
        color: white;
        font-weight: bold;
    }
    .success-message {
        padding: 10px;
        background-color: #d4edda;
        color: #155724;
        border-radius: 5px;
        margin: 10px 0;
    }
    .info-message {
        padding: 10px;
        background-color: #d1ecf1;
        color: #0c5460;
        border-radius: 5px;
        margin: 10px 0;
    }
</style>
""", unsafe_allow_html=True)

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
        """Establish telnet connection using pexpect"""
        self.add_log(f"Connecting to {self.HOST}:{self.PORT}...")
        try:
            # Use pexpect with telnet
            tn = pexpect.spawn(f'telnet {self.HOST} {self.PORT}', timeout=30)
            tn.logfile = sys.stdout.buffer
            time.sleep(self.DELAY_LONG)
            tn.sendline("")
            time.sleep(self.DELAY_SHORT)
            tn.sendline(self.USERNAME)
            time.sleep(self.DELAY_LONG)
            tn.sendline(self.PASSWORD)
            time.sleep(self.DELAY_LONG)
            tn.sendline(self.MAIN_MENU_1)
            time.sleep(self.DELAY_LONG)
            tn.sendline(self.SUB_MENU_7)
            time.sleep(self.DELAY_LONG)
            self.add_log("Telnet login and navigation successful")
            return tn
        except Exception as e:
            self.add_log(f"Telnet connection failed: {str(e)}")
            raise
    
    def scan_case(self, tn, case_id, location_id):
        """Perform a single scan"""
        try:
            tn.sendline(case_id)
            time.sleep(self.DELAY_SHORT)
            tn.sendline(location_id)
            time.sleep(self.PAUSE_BETWEEN_CASES)
            return "Success"
        except Exception as e:
            self.add_log(f"Scan error: {str(e)[:100]}")
            return "Error"
    
    def process(self):
        try:
            self.add_log("=" * 50)
            self.add_log("STARTING TELNET PROCESSING")
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
                    
                    tn = None
                    try:
                        tn = self.connect_and_login()
                        result = self.scan_case(tn, case_id, location_id)
                        self.df_valid.at[index, 'Result'] = result
                        
                        if result == "Success":
                            self.status['success_count'] += 1
                            self.add_log(f"✅ Success")
                        else:
                            self.status['error_count'] += 1
                            self.add_log(f"❌ Error")
                        
                        self.status['processed_count'] += 1
                        self.status_queue.put({'type': 'status', 'status': self.status})
                        time.sleep(1)
                        
                    except Exception as e:
                        self.add_log(f"❌ Failed: {str(e)[:100]}")
                        self.df_valid.at[index, 'Result'] = "Error"
                        self.status['processed_count'] += 1
                        self.status['error_count'] += 1
                        self.status_queue.put({'type': 'status', 'status': self.status})
                    
                    finally:
                        if tn:
                            try:
                                tn.close()
                            except:
                                pass
            
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
        error_count = len(df_valid[df_valid['Result'] == 'Error']) if not df_valid.empty else 0
        
        subject = f"Telnet Processing Complete - {status['filename']}"
        
        body = f"""
Warehouse Telnet Processing Summary
===================================
File Processed: {status['filename']}
Processing Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Batch ID: {status.get('batch_id', 'N/A')}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FINAL RESULTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 TOTAL CASES RECEIVED: {status['total_count']}
   ├─ ✅ Valid Format (Processed): {status['valid_count']}
   │   ├─ Successfully Scanned: {success_count}
   │   └─ Failed Scans: {error_count}
   └─ ❌ Invalid Format (Skipped): {status['invalid_count']}

This is an automated message from the Warehouse Telnet System.
"""
        
        msg = MIMEMultipart()
        msg['From'] = SMTP_CONFIG["username"]
        msg['To'] = ", ".join(SMTP_CONFIG["to_emails"])
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))
        
        # Create Excel file
        excel_buffer = BytesIO()
        with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
            if not df_valid.empty:
                df_valid.to_excel(writer, sheet_name='Valid_Cases', index=False)
            if not df_invalid.empty:
                df_invalid.to_excel(writer, sheet_name='Invalid_Format', index=False)
        
        excel_buffer.seek(0)
        excel_attachment = MIMEBase('application', 'vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        excel_attachment.set_payload(excel_buffer.read())
        encoders.encode_base64(excel_attachment)
        excel_attachment.add_header('Content-Disposition', 'attachment', filename=f'Telnet_Results_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx')
        msg.attach(excel_attachment)
        
        # Send email
        with smtplib.SMTP(SMTP_CONFIG["server"], SMTP_CONFIG["port"]) as server:
            server.starttls()
            server.login(SMTP_CONFIG["username"], SMTP_CONFIG["password"])
            server.send_message(msg)
        
        return True
    except Exception as e:
        print(f"Email error: {str(e)}")
        return False

# ============= VALIDATION FUNCTION =============
def validate_location_id(location_id):
    if pd.isna(location_id) or location_id == '':
        return False
    location_str = str(location_id).strip().upper()
    pattern = r'^M\d{7}$'
    return bool(re.match(pattern, location_str))

# ============= SESSION STATE INIT =============
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
    st.markdown("---")
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        password = st.text_input("Enter Access Code", type="password", key="login_pass")
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

with st.sidebar:
    if st.button("Logout", use_container_width=True):
        st.session_state.authenticated = False
        st.rerun()
    st.divider()
    st.markdown("""
    **Instructions:**
    1. Upload Excel (CaseID & LocationID)
    2. Click Start Processing
    3. Results emailed automatically
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
            
            st.metric("✅ Valid Cases", len(df_valid))
            st.metric("❌ Invalid Cases", len(df_invalid))
            
            if st.button("🚀 Start Processing", type="primary"):
                if len(df_valid) > 0:
                    batch_id = datetime.now().strftime('%Y%m%d_%H%M%S')
                    st.session_state.status_queue = queue.Queue()
                    st.session_state.processing_active = True
                    
                    processor = TelnetProcessor(df_valid, df_invalid, batch_id, st.session_state.status_queue)
                    processor.status['filename'] = uploaded_file.name
                    thread = threading.Thread(target=processor.process, daemon=True)
                    thread.start()
                    st.session_state.processor = processor
                    st.success("Processing started!")
                    st.rerun()
        else:
            st.error("Missing LocationID column")

with col2:
    if st.session_state.current_status:
        status = st.session_state.current_status
        st.metric("Processed", status.get('processed_count', 0))
        st.metric("Success", status.get('success_count', 0))
        st.metric("Errors", status.get('error_count', 0))
        
        if status.get('completed', False) and not status.get('email_sent', False):
            if st.session_state.final_df_valid is not None:
                with st.spinner("Sending email..."):
                    log_content = "\n".join(status.get('recent_logs', []))
                    if send_completion_email(status, st.session_state.final_df_valid, st.session_state.final_df_invalid, log_content):
                        status['email_sent'] = True
                        st.success("Email sent!")
                        st.balloons()

if st.session_state.processing_active:
    time.sleep(2)
    st.rerun()
