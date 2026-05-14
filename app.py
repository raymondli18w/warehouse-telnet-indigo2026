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
import telnetlib

# Page configuration
st.set_page_config(
    page_title="Warehouse Telnet Processor",
    page_icon="📦",
    layout="wide"
)

# Custom CSS for console log
st.markdown("""
<style>
    .console-log {
        background-color: #1e1e1e;
        color: #d4d4d4;
        font-family: 'Courier New', monospace;
        padding: 10px;
        border-radius: 5px;
        height: 400px;
        overflow-y: auto;
        font-size: 12px;
    }
    .stButton > button {
        width: 100%;
        font-weight: bold;
    }
</style>
""", unsafe_allow_html=True)

# ============= TELNET WORKER CLASS WITH PROPER TIMING =============
class TelnetProcessor:
    def __init__(self, df_valid, df_invalid, batch_id, status_queue, console_queue):
        self.df_valid = df_valid.copy()
        self.df_invalid = df_invalid.copy()
        self.batch_id = batch_id
        self.status_queue = status_queue
        self.console_queue = console_queue
        
        # Configuration
        self.HOST = "18whe.camelot3plcloud.com"
        self.PORT = 6667
        self.USERNAME = "bhunt"
        self.PASSWORD = "123"
        self.MAIN_MENU_1 = "1"
        self.SUB_MENU_7 = "7"
        
        # Timing delays (increased for reliability)
        self.AFTER_CONNECT_DELAY = 2    # Wait 2 seconds after connecting
        self.BETWEEN_COMMANDS_DELAY = 1  # Wait 1 second between each command
        self.AFTER_LOGIN_DELAY = 2       # Wait 2 seconds after login
        self.PAUSE_BETWEEN_CASES = 2     # Wait 2 seconds between cases
        
        # Console buffer
        self.console_lines = []
        
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
            'console_output': [],
            'filename': '',
            'batch_id': batch_id,
            'email_sent': False
        }
    
    def add_console(self, message, is_error=False):
        """Add message to console log"""
        timestamp = datetime.now().strftime('%H:%M:%S')
        if is_error:
            log_line = f"[{timestamp}] ❌ {message}"
        else:
            log_line = f"[{timestamp}] {message}"
        
        self.console_lines.append(log_line)
        if len(self.console_lines) > 20:
            self.console_lines = self.console_lines[-20:]
        
        self.status['console_output'] = self.console_lines.copy()
        self.console_queue.put({'type': 'console', 'lines': self.console_lines.copy()})
        print(log_line)
    
    def add_log(self, message):
        timestamp = datetime.now().strftime('%H:%M:%S')
        log_entry = f"[{timestamp}] {message}"
        self.status['recent_logs'].append(log_entry)
        if len(self.status['recent_logs']) > 50:
            self.status['recent_logs'] = self.status['recent_logs'][-50:]
        self.status_queue.put({'type': 'log', 'message': log_entry})
        self.add_console(message)
        print(log_entry)
    
    def connect_and_login(self):
        """Establish telnet connection with proper timing"""
        self.add_console("=" * 50)
        self.add_console("STARTING TELNET CONNECTION")
        
        try:
            # Step 1: Connect
            self.add_console(f"Step 1: Connecting to {self.HOST}:{self.PORT}...")
            tn = telnetlib.Telnet(self.HOST, self.PORT, timeout=30)
            self.add_console(f"✅ Connected successfully!")
            
            # Step 2: Wait 2 seconds after connection
            self.add_console(f"⏱️ Waiting {self.AFTER_CONNECT_DELAY} second(s) after connection...")
            time.sleep(self.AFTER_CONNECT_DELAY)
            
            # Step 3: Send initial Enter
            self.add_console(f"Step 2: Sending initial Enter...")
            tn.write(b"\r\n")
            self.add_console(f"✅ Enter sent")
            self.add_console(f"⏱️ Waiting {self.BETWEEN_COMMANDS_DELAY} second(s)...")
            time.sleep(self.BETWEEN_COMMANDS_DELAY)
            
            # Step 4: Send username
            self.add_console(f"Step 3: Sending username: {self.USERNAME}")
            tn.write(f"{self.USERNAME}\r\n".encode())
            self.add_console(f"✅ Username sent")
            self.add_console(f"⏱️ Waiting {self.AFTER_LOGIN_DELAY} second(s)...")
            time.sleep(self.AFTER_LOGIN_DELAY)
            
            # Step 5: Send password
            self.add_console(f"Step 4: Sending password...")
            tn.write(f"{self.PASSWORD}\r\n".encode())
            self.add_console(f"✅ Password sent")
            self.add_console(f"⏱️ Waiting {self.AFTER_LOGIN_DELAY} second(s)...")
            time.sleep(self.AFTER_LOGIN_DELAY)
            
            # Step 6: Navigate to main menu (option 1)
            self.add_console(f"Step 5: Navigating to main menu - sending '{self.MAIN_MENU_1}'")
            tn.write(f"{self.MAIN_MENU_1}\r\n".encode())
            self.add_console(f"✅ Menu option {self.MAIN_MENU_1} sent")
            self.add_console(f"⏱️ Waiting {self.BETWEEN_COMMANDS_DELAY} second(s)...")
            time.sleep(self.BETWEEN_COMMANDS_DELAY)
            
            # Step 7: Navigate to sub menu (option 7)
            self.add_console(f"Step 6: Navigating to sub menu - sending '{self.SUB_MENU_7}'")
            tn.write(f"{self.SUB_MENU_7}\r\n".encode())
            self.add_console(f"✅ Menu option {self.SUB_MENU_7} sent")
            self.add_console(f"⏱️ Waiting {self.BETWEEN_COMMANDS_DELAY} second(s)...")
            time.sleep(self.BETWEEN_COMMANDS_DELAY)
            
            self.add_console("=" * 50)
            self.add_console("✅ TELNET LOGIN AND NAVIGATION COMPLETE!")
            self.add_console("=" * 50)
            
            return tn
            
        except Exception as e:
            self.add_console(f"❌ TELNET ERROR: {str(e)}", is_error=True)
            self.add_log(f"Telnet connection failed: {str(e)}")
            return None
    
    def scan_case(self, tn, case_id, location_id):
        """Perform a single scan with proper timing"""
        try:
            self.add_console("-" * 40)
            self.add_console(f"SCANNING CASE")
            self.add_console(f"CaseID: {case_id}")
            self.add_console(f"LocationID: {location_id}")
            
            # Step 1: Send CaseID
            self.add_console(f"Sending CaseID: {case_id}")
            tn.write(f"{case_id}\r\n".encode())
            self.add_console(f"✅ CaseID sent")
            self.add_console(f"⏱️ Waiting {self.BETWEEN_COMMANDS_DELAY} second(s)...")
            time.sleep(self.BETWEEN_COMMANDS_DELAY)
            
            # Step 2: Send LocationID
            self.add_console(f"Sending LocationID: {location_id}")
            tn.write(f"{location_id}\r\n".encode())
            self.add_console(f"✅ LocationID sent")
            self.add_console(f"⏱️ Waiting {self.PAUSE_BETWEEN_CASES} second(s) for processing...")
            time.sleep(self.PAUSE_BETWEEN_CASES)
            
            # Step 3: Read response if available
            try:
                self.add_console(f"Checking for response...")
                response = tn.read_very_eager().decode('utf-8', errors='ignore')
                if response:
                    self.add_console(f"RESPONSE RECEIVED:")
                    for line in response.split('\n')[:5]:  # Show first 5 lines
                        self.add_console(f"  {line[:100]}")
                    if "error" in response.lower():
                        self.add_console(f"❌ Error detected in response", is_error=True)
                        return "Error"
                else:
                    self.add_console(f"No response received (normal)")
            except Exception as e:
                self.add_console(f"Could not read response: {str(e)[:50]}")
            
            self.add_console(f"✅ SCAN COMPLETE - SUCCESS")
            return "Success"
            
        except Exception as e:
            self.add_console(f"❌ SCAN ERROR: {str(e)[:100]}", is_error=True)
            return "Error"
    
    def process(self):
        try:
            self.add_console("=" * 60)
            self.add_console("STARTING TELNET PROCESSING SYSTEM")
            self.add_console(f"Total valid cases to process: {self.status['valid_count']}")
            self.add_console(f"Total invalid cases (skipped): {self.status['invalid_count']}")
            self.add_console("=" * 60)
            
            if not self.df_valid.empty:
                if 'Result' not in self.df_valid.columns:
                    self.df_valid['Result'] = ""
                
                for index, row in self.df_valid.iterrows():
                    case_id = str(row['CaseID']).zfill(20)
                    location_id = str(row['LocationID']).strip().upper()
                    
                    self.add_console(f"\n{'='*40}")
                    self.add_console(f"Processing case {index+1} of {self.status['valid_count']}")
                    self.add_console(f"{'='*40}")
                    
                    self.status['current_case'] = f"CaseID: {case_id}, Location: {location_id}"
                    self.status_queue.put({'type': 'status', 'status': self.status})
                    
                    tn = None
                    try:
                        # Connect for each case
                        tn = self.connect_and_login()
                        
                        if tn:
                            result = self.scan_case(tn, case_id, location_id)
                            self.df_valid.at[index, 'Result'] = result
                            
                            if result == "Success":
                                self.status['success_count'] += 1
                                self.add_console(f"🎉 RESULT: SUCCESS for {case_id}")
                            else:
                                self.status['error_count'] += 1
                                self.add_console(f"❌ RESULT: ERROR for {case_id}", is_error=True)
                        else:
                            self.df_valid.at[index, 'Result'] = "Error"
                            self.status['error_count'] += 1
                            self.add_console(f"❌ RESULT: ERROR - No telnet connection", is_error=True)
                        
                        self.status['processed_count'] += 1
                        self.status_queue.put({'type': 'status', 'status': self.status})
                        
                        # Small pause between cases
                        time.sleep(1)
                        
                    except Exception as e:
                        self.add_console(f"❌ EXCEPTION: {str(e)[:100]}", is_error=True)
                        self.df_valid.at[index, 'Result'] = "Error"
                        self.status['processed_count'] += 1
                        self.status['error_count'] += 1
                        self.status_queue.put({'type': 'status', 'status': self.status})
                    
                    finally:
                        if tn:
                            try:
                                tn.close()
                                self.add_console(f"🔌 Telnet connection closed for this case")
                            except:
                                pass
            
            # Mark invalid cases
            if not self.df_invalid.empty:
                if 'Result' not in self.df_invalid.columns:
                    self.df_invalid['Result'] = "Invalid Format"
            
            self.add_console("\n" + "=" * 60)
            self.add_console("PROCESSING COMPLETE - FINAL SUMMARY")
            self.add_console("=" * 60)
            self.add_console(f"Total cases received: {self.status['total_count']}")
            self.add_console(f"✅ Valid format (processed): {self.status['valid_count']}")
            self.add_console(f"   ├─ Successfully scanned: {self.status['success_count']}")
            self.add_console(f"   └─ Failed scans: {self.status['error_count']}")
            self.add_console(f"❌ Invalid format (skipped): {self.status['invalid_count']}")
            self.add_console("=" * 60)
            
            self.status['completed'] = True
            self.status_queue.put({'type': 'status', 'status': self.status})
            self.status_queue.put({'type': 'complete', 'df_valid': self.df_valid, 'df_invalid': self.df_invalid})
                
        except Exception as e:
            self.add_console(f"💥 FATAL ERROR: {str(e)}", is_error=True)
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
        
        subject = f"Telnet Processing Complete - {status['filename']}"
        
        body = f"""
Warehouse Telnet Processing Summary
===================================
File: {status['filename']}
Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Batch ID: {status['batch_id']}

RESULTS:
- Total Cases: {status['total_count']}
- Valid Format: {status['valid_count']}
- Successfully Scanned: {success_count}
- Failed Scans: {status['error_count']}
- Invalid Format (Skipped): {status['invalid_count']}

CONSOLE LOG (Last 20 lines):
{chr(10).join(status.get('console_output', [])[-20:])}

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
            df_valid.to_excel(writer, sheet_name='Valid_Cases', index=False)
            df_invalid.to_excel(writer, sheet_name='Invalid_Format', index=False)
        
        excel_buffer.seek(0)
        excel_attachment = MIMEBase('application', 'vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        excel_attachment.set_payload(excel_buffer.read())
        encoders.encode_base64(excel_attachment)
        excel_attachment.add_header('Content-Disposition', 'attachment', filename=f'Telnet_Results_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx')
        msg.attach(excel_attachment)
        
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

# ============= SESSION STATE =============
if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False
if 'processing_thread' not in st.session_state:
    st.session_state.processing_thread = None
if 'status_queue' not in st.session_state:
    st.session_state.status_queue = None
if 'console_queue' not in st.session_state:
    st.session_state.console_queue = None
if 'processing_active' not in st.session_state:
    st.session_state.processing_active = False
if 'current_status' not in st.session_state:
    st.session_state.current_status = None
if 'final_df_valid' not in st.session_state:
    st.session_state.final_df_valid = None
if 'final_df_invalid' not in st.session_state:
    st.session_state.final_df_invalid = None
if 'console_lines' not in st.session_state:
    st.session_state.console_lines = []

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

with st.sidebar:
    if st.button("Logout", use_container_width=True):
        st.session_state.authenticated = False
        st.rerun()
    st.divider()
    st.markdown("""
    **Instructions:**
    1. Upload Excel (CaseID & LocationID columns)
    2. Click Start Processing
    3. Watch the console log on the right
    4. Results will be emailed
    
    **Timing:**
    - 2 sec after connect
    - 1 sec between commands
    - 2 sec after login
    """)

# Main area - three columns
col1, col2, col3 = st.columns([2, 1, 2])

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
                    st.session_state.console_queue = queue.Queue()
                    st.session_state.processing_active = True
                    st.session_state.final_df_valid = None
                    st.session_state.final_df_invalid = None
                    st.session_state.console_lines = []
                    
                    processor = TelnetProcessor(df_valid, df_invalid, batch_id, st.session_state.status_queue, st.session_state.console_queue)
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

with col3:
    st.subheader("🖥️ Telnet Console Log")
    st.caption("Showing live telnet session with timings")
    
    # Update console display
    if st.session_state.console_queue:
        try:
            while True:
                update = st.session_state.console_queue.get_nowait()
                if update['type'] == 'console':
                    st.session_state.console_lines = update['lines']
        except queue.Empty:
            pass
    
    # Display console
    if st.session_state.console_lines:
        console_text = "\n".join(st.session_state.console_lines)
        st.code(console_text, language="bash")
    else:
        st.info("Console will appear here when processing starts...")
    
    # Button to copy console log
    if st.session_state.console_lines:
        console_text = "\n".join(st.session_state.console_lines)
        st.download_button(
            label="📋 Copy Console Log",
            data=console_text,
            file_name=f"console_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
            mime="text/plain"
        )

# Auto-refresh
if st.session_state.processing_active:
    time.sleep(2)
    st.rerun()
