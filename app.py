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
        font-weight: bold;
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
    
    def pause(self):
        self.paused = True
        self.status['paused'] = True
        self.add_log("⏸️ Processing PAUSED")
        self.status_queue.put({'type': 'status', 'status': self.status})
    
    def resume(self):
        self.paused = False
        self.status['paused'] = False
        self.add_log("▶️ Processing RESUMED")
        self.status_queue.put({'type': 'status', 'status': self.status})
    
    def stop(self):
        self.stopped = True
        self.status['stopped'] = True
        self.add_log("🛑 Processing STOPPED")
        self.status_queue.put({'type': 'status', 'status': self.status})
    
    def process(self):
        try:
            self.add_log("=" * 50)
            self.add_log("STARTING TELNET PROCESSING")
            self.add_log(f"Valid cases: {self.status['valid_count']}")
            self.add_log("=" * 50)
            
            if not self.df_valid.empty:
                if 'Result' not in self.df_valid.columns:
                    self.df_valid['Result'] = ""
                
                for index in range(self.current_index, len(self.df_valid)):
                    if self.stopped:
                        break
                    
                    while self.paused and not self.stopped:
                        time.sleep(1)
                    
                    if self.stopped:
                        break
                    
                    row = self.df_valid.iloc[index]
                    case_id = str(row['CaseID']).zfill(20)
                    location_id = str(row['LocationID']).strip().upper()
                    
                    self.current_index = index
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
                            tn.close()
            
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

# ... (rest of the validation and UI code remains the same as before)
