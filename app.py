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
import wexpect
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
    .pause-button > button {
        background-color: #ffc107;
        color: #000;
    }
    .resume-button > button {
        background-color: #28a745;
        color: white;
    }
    .stop-button > button {
        background-color: #dc3545;
        color: white;
    }
</style>
""", unsafe_allow_html=True)

# ============= TELNET WORKER CLASS WITH PAUSE =============
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
        
        # Control flags
        self.paused = False
        self.stopped = False
        self.current_index = 0
        
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
            'paused': False,
            'stopped': False,
            'recent_logs': [],
            'filename': '',
            'batch_id': batch_id,
            'email_sent': False,
            'partial_email_sent': False
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
        """Establish telnet connection, login, and navigate to menu"""
        self.add_log(f"Connecting to {self.HOST}:{self.PORT}...")
        try:
            tn = wexpect.spawn(f"telnet {self.HOST} {self.PORT}", timeout=30)
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
        """Perform a single scan using existing telnet connection"""
        try:
            tn.sendline(case_id)
            time.sleep(self.DELAY_SHORT)
            tn.sendline(location_id)
            time.sleep(self.PAUSE_BETWEEN_CASES)
            
            # Try to read output (optional)
            try:
                output = tn.read_nonblocking(size=1000, timeout=1)
                if output and "error" in output.lower():
                    return "Error"
            except:
                pass
            
            return "Success"
        except Exception as e:
            self.add_log(f"Scan error: {str(e)[:100]}")
            return "Error"
    
    def pause(self):
        """Pause processing"""
        self.paused = True
        self.status['paused'] = True
        self.add_log("⏸️ Processing PAUSED by user")
        self.status_queue.put({'type': 'status', 'status': self.status})
    
    def resume(self):
        """Resume processing"""
        self.paused = False
        self.status['paused'] = False
        self.add_log("▶️ Processing RESUMED")
        self.status_queue.put({'type': 'status', 'status': self.status})
    
    def stop(self):
        """Stop processing"""
        self.stopped = True
        self.status['stopped'] = True
        self.add_log("🛑 Processing STOPPED by user")
        self.status_queue.put({'type': 'status', 'status': self.status})
    
    def process(self):
        try:
            self.add_log("=" * 50)
            self.add_log("STARTING TELNET PROCESSING")
            self.add_log(f"Valid cases (correct format): {self.status['valid_count']}")
            self.add_log(f"Invalid cases (wrong format): {self.status['invalid_count']}")
            self.add_log("=" * 50)
            
            # Process valid cases
            if not self.df_valid.empty:
                self.add_log(f"\nProcessing {len(self.df_valid)} valid cases...")
                
                # Add Result column if not present
                if 'Result' not in self.df_valid.columns:
                    self.df_valid['Result'] = ""
                
                # Start from where we left off
                for index in range(self.current_index, len(self.df_valid)):
                    # Check for stop signal
                    if self.stopped:
                        self.add_log("Processing stopped before completing all cases")
                        break
                    
                    # Check for pause
                    while self.paused and not self.stopped:
                        time.sleep(1)
                        if self.stopped:
                            break
                    
                    if self.stopped:
                        break
                    
                    row = self.df_valid.iloc[index]
                    case_id = str(row['CaseID']).zfill(20)
                    location_id = str(row['LocationID']).strip().upper()
                    
                    self.current_index = index
                    self.status['current_case'] = f"CaseID: {case_id}, Location: {location_id}"
                    self.add_log(f"Processing {index+1}/{self.status['valid_count']}: {case_id} -> {location_id}")
                    self.status_queue.put({'type': 'status', 'status': self.status})
                    
                    tn = None
                    try:
                        tn = self.connect_and_login()
                        result = self.scan_case(tn, case_id, location_id)
                        self.df_valid.at[index, 'Result'] = result
                        
                        if result == "Success":
                            self.add_log(f"✅ Success for {case_id}")
                            self.status['success_count'] += 1
                        else:
                            self.add_log(f"❌ Error for {case_id}")
                            self.status['error_count'] += 1
                        
                        self.status['processed_count'] += 1
                        self.status_queue.put({'type': 'status', 'status': self.status})
                        time.sleep(1)
                        
                    except Exception as e:
                        self.add_log(f"❌ Failed to process {case_id}: {str(e)[:100]}")
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
            if not self.df_invalid.empty and not self.stopped:
                if 'Result' not in self.df_invalid.columns:
                    self.df_invalid['Result'] = "Invalid Format"
                for index, row in self.df_invalid.iterrows():
                    self.df_invalid.at[index, 'Result'] = "Invalid Format - LocationID must be M followed by 7 digits"
            
            if not self.stopped:
                self.add_log("=" * 50)
                self.add_log("PROCESSING COMPLETE")
                self.add_log(f"Total cases received: {self.status['total_count']}")
                self.add_log(f"  - Valid format (processed): {self.status['valid_count']}")
                self.add_log(f"    • Successfully scanned: {self.status['success_count']}")
                self.add_log(f"    • Failed scans: {self.status['error_count']}")
                self.add_log(f"  - Invalid format (skipped): {self.status['invalid_count']}")
                self.add_log("=" * 50)
                self.status['completed'] = True
            else:
                self.add_log("=" * 50)
                self.add_log("PROCESSING STOPPED EARLY")
                self.add_log(f"Processed {self.status['processed_count']} out of {self.status['valid_count']} valid cases")
                self.add_log("=" * 50)
            
            self.status_queue.put({'type': 'status', 'status': self.status})
            self.status_queue.put({'type': 'complete', 'df_valid': self.df_valid, 'df_invalid': self.df_invalid})
                
        except Exception as e:
            self.add_log(f"FATAL ERROR: {str(e)}")
            self.status['completed'] = True
            self.status_queue.put({'type': 'status', 'status': self.status})

# ============= EMAIL FUNCTION =============
def send_completion_email(status, df_valid, df_invalid, log_content, is_partial=False):
    SMTP_CONFIG = {
        "server": "smtp.office365.com",
        "port": 587,
        "username": "donotreply@18wheels.ca",
        "password": "E@B9KCE7Z**ejRq",
        "to_emails": ["Raymond.li@18wheels.ca", "customer.service@18wheels.ca"]
    }
    
    try:
        # Calculate final numbers
        success_count = len(df_valid[df_valid['Result'] == 'Success']) if not df_valid.empty else 0
        error_count = len(df_valid[df_valid['Result'] == 'Error']) if not df_valid.empty else 0
        
        if is_partial:
            subject = f"PARTIAL Results - Telnet Processing - {status['filename']}"
        else:
            subject = f"COMPLETE Results - Telnet Processing - {status['filename']}"
        
        # Email body
        body = f"""
Warehouse Telnet Processing Summary
===================================
File Processed: {status['filename']}
Processing Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Batch ID: {status.get('batch_id', 'N/A')}
{"⚠️ PARTIAL REPORT - Processing was stopped before completion" if is_partial else "✅ FINAL REPORT - Processing completed"}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FINAL RESULTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 TOTAL CASES RECEIVED: {status['total_count']}
   ├─ ✅ Valid Format (To Process): {status['valid_count']}
   │   ├─ Successfully Scanned: {success_count}
   │   ├─ Failed Scans: {error_count}
   │   └─ Not Processed: {status['valid_count'] - status['processed_count'] if is_partial else 0}
   └─ ❌ Invalid Format (Skipped): {status['invalid_count']}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PROGRESS SUMMARY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Processed: {status['processed_count']}/{status['valid_count']} valid cases ({status['processed_count']/status['valid_count']*100:.1f}% complete if is_partial else "100%")

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
VERIFICATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Expected Total: {status['total_count']}
Actual Scanned: {success_count}
Difference: {status['total_count'] - success_count}

{"✅ PERFECT MATCH - All scanned successfully!" if success_count == status['processed_count'] and not is_partial else "⚠️ PARTIAL COMPLETION - Not all cases were processed" if is_partial else "⚠️ REVIEW REQUIRED - Some scans failed"}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INVALID FORMAT DETAILS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

LocationID must be in format: M followed by exactly 7 numbers (e.g., M0146687)

{chr(10).join([f"• {row['CaseID']} - {row['LocationID']}" for _, row in df_invalid.head(20).iterrows()]) if not df_invalid.empty else "No invalid cases found."}
{"..." if len(df_invalid) > 20 else ""}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ATTACHMENTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Results.xlsx - Full results with 2 tabs:
   - Tab 1: Valid Cases (with Success/Error/Not Processed status)
   - Tab 2: Invalid Format Cases

2. Processing_Log.txt - Full console log of the run

This is an automated message from the Warehouse Telnet System.
Please do not reply to this email.
"""
        
        msg = MIMEMultipart()
        msg['From'] = SMTP_CONFIG["username"]
        msg['To'] = ", ".join(SMTP_CONFIG["to_emails"])
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))
        
        # Add a "Not Processed" status for remaining cases if partial
        if is_partial and status['processed_count'] < status['valid_count']:
            df_not_processed = df_valid[status['processed_count']:].copy()
            df_not_processed['Result'] = "Not Processed (Stopped)"
            df_processed = df_valid[:status['processed_count']].copy()
            df_combined = pd.concat([df_processed, df_not_processed], ignore_index=True)
        else:
            df_combined = df_valid
        
        # Create Excel file with two tabs
        excel_buffer = BytesIO()
        with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
            if not df_combined.empty:
                df_combined.to_excel(writer, sheet_name='Valid_Cases', index=False)
            else:
                pd.DataFrame({'Message': ['No valid cases found']}).to_excel(writer, sheet_name='Valid_Cases', index=False)
            
            if not df_invalid.empty:
                df_invalid.to_excel(writer, sheet_name='Invalid_Format', index=False)
            else:
                pd.DataFrame({'Message': ['No invalid cases found']}).to_excel(writer, sheet_name='Invalid_Format', index=False)
        
        excel_buffer.seek(0)
        excel_attachment = MIMEBase('application', 'vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        excel_attachment.set_payload(excel_buffer.read())
        encoders.encode_base64(excel_attachment)
        excel_attachment.add_header('Content-Disposition', 'attachment', filename=f'Telnet_Results_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx')
        msg.attach(excel_attachment)
        
        # Attach log file
        log_attachment = MIMEBase('text', 'plain')
        log_attachment.set_payload(log_content)
        encoders.encode_base64(log_attachment)
        log_attachment.add_header('Content-Disposition', 'attachment', filename=f'Processing_Log_{datetime.now().strftime("%Y%m%d_%H%M%S")}.txt')
        msg.attach(log_attachment)
        
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
    """Validate LocationID format: M followed by exactly 7 numbers"""
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
if 'processor' not in st.session_state:
    st.session_state.processor = None

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
                st.error("❌ Invalid access code. Please try again.")
    st.markdown("---")
    st.caption("Authorized personnel only.")
    st.stop()

# ============= MAIN APP =============
st.title("📦 Warehouse Telnet Processor")
st.markdown(f"**Logged in:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# Sidebar
with st.sidebar:
    st.header("Session")
    if st.button("Logout", use_container_width=True):
        st.session_state.authenticated = False
        st.rerun()
    
    st.divider()
    st.header("Instructions")
    st.markdown("""
    1. **Upload Excel file** with columns:
       - `CaseID` or `CASEID`
       - `LocationID` or `LOCATIONID`
    
    2. **LocationID Format:** Must be `M` followed by exactly 7 numbers
       - ✅ Valid: M0146687, M1234567
       - ❌ Invalid: M123456, 0146687, ABC123
    
    3. **Control Buttons:**
       - ⏸️ **Pause** - Stops temporarily, can resume
       - ▶️ **Resume** - Continues from where paused
       - 🛑 **Stop & Email** - Stops and sends partial results
    
    4. **Results emailed automatically** with:
       - Excel file (2 tabs: Valid & Invalid)
       - Text log file
    """)
    
    st.divider()
    st.info("🔧 **How it works:** Each valid case gets its own fresh telnet connection. Invalid cases are skipped and reported.")
    
    st.caption(f"Menu: 1→7 • Pause/Resume • v4.0")

# Main area - two columns
col1, col2 = st.columns([2, 1])

with col1:
    st.subheader("📁 File Upload")
    uploaded_file = st.file_uploader("Choose Excel file (.xlsx)", type=['xlsx'], key="file_uploader")
    
    if uploaded_file and not st.session_state.processing_active:
        # Read and validate data
        df_raw = pd.read_excel(uploaded_file, dtype=str, keep_default_na=False)
        
        # Standardize column names
        if 'CASEID' in df_raw.columns:
            df_raw.rename(columns={'CASEID': 'CaseID'}, inplace=True)
        if 'LOCATIONID' in df_raw.columns:
            df_raw.rename(columns={'LOCATIONID': 'LocationID'}, inplace=True)
        
        # Validate format
        if 'LocationID' in df_raw.columns:
            df_raw['Valid_Format'] = df_raw['LocationID'].apply(validate_location_id)
            df_valid = df_raw[df_raw['Valid_Format'] == True].copy()
            df_invalid = df_raw[df_raw['Valid_Format'] == False].copy()
            
            # Drop the temporary column
            df_valid = df_valid.drop(columns=['Valid_Format'])
            df_invalid = df_invalid.drop(columns=['Valid_Format'])
            
            # Display validation summary
            st.subheader("📊 Validation Summary")
            col_v1, col_v2, col_v3 = st.columns(3)
            with col_v1:
                st.metric("Total Rows", len(df_raw))
            with col_v2:
                st.metric("✅ Valid Format", len(df_valid))
            with col_v3:
                st.metric("❌ Invalid Format", len(df_invalid))
            
            # Show preview
            with st.expander("📋 Preview Valid Cases (will be processed)"):
                st.dataframe(df_valid.head(10), use_container_width=True)
            
            with st.expander("⚠️ Preview Invalid Cases (will be skipped)"):
                st.dataframe(df_invalid.head(10), use_container_width=True)
                if not df_invalid.empty:
                    st.warning("Invalid LocationID format. Must be 'M' followed by exactly 7 numbers (e.g., M0146687)")
            
            if st.button("🚀 Start Processing", type="primary", use_container_width=True):
                if len(df_valid) == 0:
                    st.error("❌ No valid cases to process. Please check LocationID format.")
                else:
                    # Initialize processing
                    batch_id = datetime.now().strftime('%Y%m%d_%H%M%S')
                    st.session_state.status_queue = queue.Queue()
                    st.session_state.processing_active = True
                    st.session_state.current_status = None
                    st.session_state.final_df_valid = None
                    st.session_state.final_df_invalid = None
                    
                    # Store filename for email
                    filename = uploaded_file.name
                    
                    # Start background thread
                    processor = TelnetProcessor(df_valid, df_invalid, batch_id, st.session_state.status_queue)
                    processor.status['filename'] = filename
                    thread = threading.Thread(target=processor.process, daemon=True)
                    thread.start()
                    st.session_state.processing_thread = thread
                    st.session_state.processor = processor
                    
                    st.success(f"✅ Processing started! Batch ID: {batch_id}")
                    st.info(f"🔄 Processing {len(df_valid)} valid cases. Use Pause/Stop buttons as needed.")
                    time.sleep(1)
                    st.rerun()
        else:
            st.error("❌ Excel file must contain 'LocationID' column")

with col2:
    st.subheader("📈 Processing Status")
    
    # Control buttons for active processing
    if st.session_state.processing_active and st.session_state.processor:
        st.subheader("🎮 Controls")
        
        col_btn1, col_btn2, col_btn3 = st.columns(3)
        with col_btn1:
            if st.button("⏸️ Pause", use_container_width=True, key="pause_btn"):
                st.session_state.processor.pause()
                st.rerun()
        
        with col_btn2:
            if st.button("▶️ Resume", use_container_width=True, key="resume_btn"):
                st.session_state.processor.resume()
                st.rerun()
        
        with col_btn3:
            if st.button("🛑 Stop & Email", use_container_width=True, key="stop_btn"):
                st.session_state.processor.stop()
                st.rerun()
        
        st.divider()
    
    # Check for updates from queue
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
    
    # Display current status
    if st.session_state.current_status:
        status = st.session_state.current_status
        
        # Status indicator
        if status.get('paused', False):
            st.info("⏸️ **STATUS: PAUSED**")
        elif status.get('stopped', False):
            st.warning("🛑 **STATUS: STOPPED**")
        elif status.get('completed', False):
            st.success("✅ **STATUS: COMPLETED**")
        else:
            st.info("▶️ **STATUS: RUNNING**")
        
        # Metrics
        col_m1, col_m2, col_m3 = st.columns(3)
        with col_m1:
            st.metric("📊 Total", status.get('total_count', 0))
        with col_m2:
            st.metric("✅ Valid", status.get('valid_count', 0))
        with col_m3:
            st.metric("❌ Invalid", status.get('invalid_count', 0))
        
        col_p1, col_p2 = st.columns(2)
        with col_p1:
            st.metric("🔄 Scanned", status.get('processed_count', 0))
        with col_p2:
            if status.get('valid_count', 0) > 0:
                progress_pct = (status.get('processed_count', 0) / status.get('valid_count', 0)) * 100
                st.metric("Progress", f"{progress_pct:.1f}%")
        
        # Progress bar
        if status.get('valid_count', 0) > 0:
            progress = status.get('processed_count', 0) / status.get('valid_count', 0)
            st.progress(progress, text=f"Processed: {status.get('processed_count', 0)}/{status.get('valid_count', 0)}")
        
        # Success/Error stats
        col_s1, col_s2 = st.columns(2)
        with col_s1:
            st.metric("✅ Success", status.get('success_count', 0), delta=f"{status.get('success_count', 0)/max(status.get('processed_count',1),1)*100:.0f}%")
        with col_s2:
            st.metric("❌ Errors", status.get('error_count', 0))
        
        st.divider()
        
        # Current activity
        st.subheader("🔄 Current Case")
        current_case = status.get('current_case', 'Waiting for start...')
        st.info(current_case)
        
        # Recent logs
        st.subheader("📝 Recent Activity (Last 10)")
        recent_logs = status.get('recent_logs', [])
        if recent_logs:
            for log in recent_logs[-10:]:
                if "✅" in log:
                    st.success(log)
                elif "❌" in log or "Error" in log:
                    st.error(log)
                elif "⏸️" in log or "▶️" in log or "🛑" in log:
                    st.warning(log)
                else:
                    st.text(log)
        else:
            st.caption("Waiting for processing to start...")
        
        # Check if processing is complete or stopped
        if (status.get('completed', False) or status.get('stopped', False)) and not status.get('email_sent', False):
            if st.session_state.final_df_valid is not None and st.session_state.final_df_invalid is not None:
                st.divider()
                with st.spinner("📧 Generating results and sending email..."):
                    # Collect all logs
                    log_content = "\n".join(status.get('recent_logs', []))
                    
                    # Determine if partial
                    is_partial = status.get('stopped', False) or (status.get('processed_count', 0) < status.get('valid_count', 0))
                    
                    # Send email
                    if send_completion_email(
                        status, 
                        st.session_state.final_df_valid, 
                        st.session_state.final_df_invalid, 
                        log_content,
                        is_partial
                    ):
                        status['email_sent'] = True
                        if is_partial:
                            st.success("📧 Partial results emailed successfully!")
                        else:
                            st.success("📧 Results emailed successfully!")
                        st.balloons()
                    else:
                        st.error("❌ Failed to send email. Please check SMTP settings.")
                
                st.divider()
                # Show final summary
                st.subheader("📊 Final Summary")
                success_count = len(st.session_state.final_df_valid[st.session_state.final_df_valid['Result'] == 'Success']) if not st.session_state.final_df_valid.empty else 0
                st.write(f"**Successfully Scanned:** {success_count}/{status.get('valid_count', 0)}")
                st.write(f"**Invalid Format Skipped:** {status.get('invalid_count', 0)}")
                
                if status.get('stopped', False):
                    st.warning(f"⚠️ Processing stopped early. {status.get('valid_count', 0) - status.get('processed_count', 0)} cases not processed.")
                elif success_count == status.get('valid_count', 0):
                    st.success("✅ All valid cases processed successfully!")
                else:
                    st.warning(f"⚠️ {status.get('valid_count', 0) - success_count} valid cases had errors")
                
                if st.button("🔄 Clear & Start New Batch", use_container_width=True):
                    st.session_state.processing_active = False
                    st.session_state.current_status = None
                    st.session_state.processing_thread = None
                    st.session_state.final_df_valid = None
                    st.session_state.final_df_invalid = None
                    st.session_state.processor = None
                    st.rerun()
    else:
        st.info("No active processing")
        st.caption("Upload an Excel file with LocationID column and click 'Start Processing'")

# Auto-refresh every 3 seconds if processing is active
if st.session_state.processing_active:
    time.sleep(3)
    st.rerun()