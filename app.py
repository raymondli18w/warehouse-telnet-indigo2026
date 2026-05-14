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
import asyncio
import telnetlib3

# Page configuration
st.set_page_config(
    page_title="Warehouse Telnet Processor",
    page_icon="📦",
    layout="wide"
)

# Custom CSS for console log
st.markdown("""
<style>
    .stButton > button {
        width: 100%;
        font-weight: bold;
    }
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
</style>
""", unsafe_allow_html=True)

# ============= TELNET WORKER CLASS =============
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
        
        # Timing delays
        self.AFTER_CONNECT_DELAY = 2
        self.BETWEEN_COMMANDS_DELAY = 1
        self.AFTER_LOGIN_DELAY = 2
        self.PAUSE_BETWEEN_CASES = 2
        
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
    
    async def connect_and_login_async(self):
        """Async telnet connection"""
        self.add_console("=" * 50)
        self.add_console("STARTING TELNET CONNECTION")
        
        try:
            # Step 1: Connect
            self.add_console(f"Step 1: Connecting to {self.HOST}:{self.PORT}...")
            reader, writer = await telnetlib3.open_connection(self.HOST, self.PORT, timeout=30)
            self.add_console(f"✅ Connected successfully!")
            
            # Step 2: Wait 2 seconds
            self.add_console(f"⏱️ Waiting {self.AFTER_CONNECT_DELAY} second(s)...")
            await asyncio.sleep(self.AFTER_CONNECT_DELAY)
            
            # Step 3: Send Enter
            self.add_console(f"Step 2: Sending Enter...")
            writer.write("\r\n")
            await writer.drain()
            self.add_console(f"✅ Enter sent")
            await asyncio.sleep(self.BETWEEN_COMMANDS_DELAY)
            
            # Step 4: Send username
            self.add_console(f"Step 3: Sending username: {self.USERNAME}")
            writer.write(f"{self.USERNAME}\r\n")
            await writer.drain()
            self.add_console(f"✅ Username sent")
            await asyncio.sleep(self.AFTER_LOGIN_DELAY)
            
            # Step 5: Send password
            self.add_console(f"Step 4: Sending password...")
            writer.write(f"{self.PASSWORD}\r\n")
            await writer.drain()
            self.add_console(f"✅ Password sent")
            await asyncio.sleep(self.AFTER_LOGIN_DELAY)
            
            # Step 6: Main menu
            self.add_console(f"Step 5: Sending menu option {self.MAIN_MENU_1}")
            writer.write(f"{self.MAIN_MENU_1}\r\n")
            await writer.drain()
            self.add_console(f"✅ Menu option sent")
            await asyncio.sleep(self.BETWEEN_COMMANDS_DELAY)
            
            # Step 7: Sub menu
            self.add_console(f"Step 6: Sending menu option {self.SUB_MENU_7}")
            writer.write(f"{self.SUB_MENU_7}\r\n")
            await writer.drain()
            self.add_console(f"✅ Menu option sent")
            await asyncio.sleep(self.BETWEEN_COMMANDS_DELAY)
            
            self.add_console("✅ TELNET LOGIN COMPLETE!")
            return writer
            
        except Exception as e:
            self.add_console(f"❌ TELNET ERROR: {str(e)}", is_error=True)
            return None
    
    async def scan_case_async(self, writer, case_id, location_id):
        """Async scan case"""
        try:
            self.add_console(f"Sending CaseID: {case_id}")
            writer.write(f"{case_id}\r\n")
            await writer.drain()
            await asyncio.sleep(self.BETWEEN_COMMANDS_DELAY)
            
            self.add_console(f"Sending LocationID: {location_id}")
            writer.write(f"{location_id}\r\n")
            await writer.drain()
            await asyncio.sleep(self.PAUSE_BETWEEN_CASES)
            
            self.add_console(f"✅ Scan complete")
            return "Success"
            
        except Exception as e:
            self.add_console(f"❌ Scan error: {str(e)}", is_error=True)
            return "Error"
    
    async def process_async(self):
        """Async main processing"""
        try:
            self.add_console("=" * 60)
            self.add_console("STARTING PROCESSING")
            self.add_console(f"Valid cases: {self.status['valid_count']}")
            self.add_console("=" * 60)
            
            if not self.df_valid.empty:
                if 'Result' not in self.df_valid.columns:
                    self.df_valid['Result'] = ""
                
                for index, row in self.df_valid.iterrows():
                    case_id = str(row['CaseID']).zfill(20)
                    location_id = str(row['LocationID']).strip().upper()
                    
                    self.add_console(f"\n--- Case {index+1}/{self.status['valid_count']} ---")
                    self.status['current_case'] = f"CaseID: {case_id}"
                    self.status_queue.put({'type': 'status', 'status': self.status})
                    
                    writer = None
                    try:
                        writer = await self.connect_and_login_async()
                        
                        if writer:
                            result = await self.scan_case_async(writer, case_id, location_id)
                            self.df_valid.at[index, 'Result'] = result
                            
                            if result == "Success":
                                self.status['success_count'] += 1
                                self.add_console(f"✅ SUCCESS for {case_id}")
                            else:
                                self.status['error_count'] += 1
                                self.add_console(f"❌ ERROR for {case_id}")
                            
                            writer.close()
                            await writer.wait_closed()
                        
                        self.status['processed_count'] += 1
                        self.status_queue.put({'type': 'status', 'status': self.status})
                        
                    except Exception as e:
                        self.add_console(f"❌ Failed: {str(e)[:100]}", is_error=True)
                        self.df_valid.at[index, 'Result'] = "Error"
                        self.status['processed_count'] += 1
                        self.status['error_count'] += 1
                        self.status_queue.put({'type': 'status', 'status': self.status})
                    
                    await asyncio.sleep(1)
            
            if not self.df_invalid.empty:
                if 'Result' not in self.df_invalid.columns:
                    self.df_invalid['Result'] = "Invalid Format"
            
            self.add_console("\n" + "=" * 60)
            self.add_console("PROCESSING COMPLETE")
            self.add_console(f"Success: {self.status['success_count']}")
            self.add_console(f"Errors: {self.status['error_count']}")
            self.add_console("=" * 60)
            
            self.status['completed'] = True
            self.status_queue.put({'type': 'status', 'status': self.status})
            self.status_queue.put({'type': 'complete', 'df_valid': self.df_valid, 'df_invalid': self.df_invalid})
            
        except Exception as e:
            self.add_console(f"FATAL ERROR: {str(e)}", is_error=True)
            self.status['completed'] = True
            self.status_queue.put({'type': 'status', 'status': self.status})
    
    def process(self):
        """Sync wrapper for async process"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self.process_async())

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
- Invalid Format: {status['invalid_count']}

CONSOLE LOG:
{chr(10).join(status.get('console_output', [])[-20:])}
"""
        
        msg = MIMEMultipart()
        msg['From'] = SMTP_CONFIG["username"]
        msg['To'] = ", ".join(SMTP_CONFIG["to_emails"])
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))
        
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
    1. Upload Excel (CaseID & LocationID)
    2. Click Start Processing
    3. Watch console log on right
    4. Results emailed automatically
    """)

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
            
            if st.button("🚀 Start Processing", type="primary"):
                if len(df_valid) > 0:
                    batch_id = datetime.now().strftime('%Y%m%d_%H%M%S')
                    st.session_state.status_queue = queue.Queue()
                    st.session_state.console_queue = queue.Queue()
                    st.session_state.processing_active = True
                    
                    processor = TelnetProcessor(df_valid, df_invalid, batch_id, st.session_state.status_queue, st.session_state.console_queue)
                    processor.status['filename'] = uploaded_file.name
                    thread = threading.Thread(target=processor.process, daemon=True)
                    thread.start()
                    st.session_state.processor = processor
                    st.success("Processing started!")
                    st.rerun()
        else:
            st.error("Missing LocationID column")

with col2:
    st.subheader("📊 Status")
    
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
        st.metric("Processed", status.get('processed_count', 0))
        st.metric("Success", status.get('success_count', 0))
        st.metric("Errors", status.get('error_count', 0))
        
        if status.get('valid_count', 0) > 0:
            progress = status.get('processed_count', 0) / status.get('valid_count', 0)
            st.progress(progress)
        
        if status.get('completed', False) and not status.get('email_sent', False):
            if st.session_state.final_df_valid is not None:
                with st.spinner("Sending email..."):
                    log_content = "\n".join(status.get('recent_logs', []))
                    if send_completion_email(status, st.session_state.final_df_valid, st.session_state.final_df_invalid, log_content):
                        status['email_sent'] = True
                        st.success("Email sent!")
                        st.balloons()

with col3:
    st.subheader("🖥️ Console Log")
    if st.session_state.console_queue:
        try:
            while True:
                update = st.session_state.console_queue.get_nowait()
                if update['type'] == 'console':
                    st.session_state.console_lines = update['lines']
        except queue.Empty:
            pass
    
    if st.session_state.console_lines:
        st.code("\n".join(st.session_state.console_lines), language="bash")
    else:
        st.info("Console will appear here...")

if st.session_state.processing_active:
    time.sleep(2)
    st.rerun()
