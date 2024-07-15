import sys
import json
import csv
import os
import re
import logging
import PyPDF2
from google.cloud import vision
from google.cloud import language_v1
from dotenv import load_dotenv
from PyQt5.QtWidgets import (QApplication, QWidget, QVBoxLayout, QPushButton, QLabel, 
                             QFileDialog, QComboBox, QTextEdit, QProgressBar)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from cryptography.fernet import Fernet

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

credentials_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
logger.info(f"Attempting to use credentials file: {credentials_path}")

os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = credentials_path

vision_client = vision.ImageAnnotatorClient()
language_client = language_v1.LanguageServiceClient()

ENCRYPTION_KEY = Fernet.generate_key()
cipher_suite = Fernet(ENCRYPTION_KEY)

def encrypt_data(data):
    return cipher_suite.encrypt(data.encode()).decode()

def decrypt_data(encrypted_data):
    return cipher_suite.decrypt(encrypted_data.encode()).decode()

def preprocess_document(file_path):
    logger.info(f"Preprocessing document: {file_path}")
    try:
        if file_path.lower().endswith('.pdf'):
            return preprocess_pdf(file_path)
        elif file_path.lower().endswith(('.png', '.jpg', '.jpeg')):
            return preprocess_image(file_path)
        else:
            with open(file_path, 'r') as file:
                return file.read()
    except Exception as e:
        logger.error(f"Error preprocessing document: {str(e)}")
        raise

def preprocess_pdf(pdf_path):
    logger.info(f"Preprocessing PDF: {pdf_path}")
    text = ""
    try:
        with open(pdf_path, 'rb') as pdf_file:
            pdf_reader = PyPDF2.PdfReader(pdf_file)
            for page in pdf_reader.pages:
                text += page.extract_text() + "\n"
        
        if not text.strip():  #if no text found then using OCR
            logger.info("No text extracted from PDF, falling back to OCR")
            return ocr_pdf(pdf_path)
        
        logger.info(f"Extracted text from PDF: {text[:100]}...")  
        return text
    except Exception as e:
        logger.error(f"Error processing PDF: {str(e)}")
        raise

def ocr_pdf(pdf_path):
    logger.info(f"Performing OCR on PDF: {pdf_path}")
    try:
        with open(pdf_path, 'rb') as pdf_file:
            content = pdf_file.read()
        
        image = vision.Image(content=content)
        response = vision_client.document_text_detection(image=image)
        text = response.full_text_annotation.text
        logger.info(f"OCR result: {text[:100]}...")  
        return text
    except Exception as e:
        logger.error(f"Error performing OCR on PDF: {str(e)}")
        raise

def preprocess_image(image_path):
    logger.info(f"Preprocessing image: {image_path}")
    try:
        with open(image_path, 'rb') as image_file:
            content = image_file.read()
        
        image = vision.Image(content=content)
        response = vision_client.text_detection(image=image)
        text = response.text_annotations[0].description if response.text_annotations else ""
        logger.info(f"Extracted text from image: {text[:100]}...")  
        return text
    except Exception as e:
        logger.error(f"Error processing image: {str(e)}")
        raise

def extract_information(text):
    logger.info("Extracting information from text")
    extracted_info = {
        'invoice_number': 'Not found',
        'invoice_date': 'Not found',
        'amount_due': 'Not found',
        'due_date': 'Not found',
        'account_number': 'Not found',
        'billing_period': 'Not found',
        'consumer_number': 'Not found',
        'product': 'Not found',
        'quantity': 'Not found',
        'rate': 'Not found'
    }
    
    # Patterns for different bill types
    patterns = {
        'invoice_number': r'Invoice Number[.:]\s*(\w+)',
        'invoice_date': r'(Invoice Date|Date)[.:]\s*(\d{2}[./-]\d{2}[./-]\d{4})',
        'amount_due': r'(Total Amount Due|TOTAL AMOUNT DUE|Amount|Total Invoice Amount|Amount Tendered|Due Amount|Current demand|To the total due date)[.:]?\s*(?:INR|Rs\.?)?\s*([\d,.]+)',
        'due_date': r'Due Date[.:]\s*(\d{2}[./-]\d{2}[./-]\d{4})',
        'account_number': r'(Account Number|BUSINESS PARTNER NO\.)[.:]\s*(\w+)',
        'billing_period': r'Billing Period[.:]\s*([\d/]+ to [\d/]+)',
        'consumer_number': r'Consumer Number[.:]\s*(\w+)',
        'product': r'Product\s+(PETROL|\w+)',
        'quantity': r'(Qty|quantity)\s+([\d.]+)',
        'rate': r'(Rate-Rs|Price/SCM in INR)\s+([\d.]+)'
    }
    
    for key, pattern in patterns.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            logger.debug(f"Pattern matched for {key}: {match.groups()}")
            extracted_info[key] = match.group(1) if len(match.groups()) == 1 else match.group(2)
    
    logger.info(f"Extracted information: {extracted_info}")
    return '\n'.join([f"{k.replace('_', ' ').title()}: {v}" for k, v in extracted_info.items()])

def validate_extracted_info(extracted_text):
    logger.info("Validating extracted information")
    validated_info = {}

    # Extract information from the structured output
    for line in extracted_text.split('\n'):
        if ':' in line:
            key, value = line.split(':', 1)
            key = key.strip().lower().replace(' ', '_')
            value = value.strip()
            
            if value != "Not found":
                validated_info[key] = value

    logger.info(f"Validated information: {validated_info}")
    return validated_info

class ExtractionThread(QThread):
    update_progress = pyqtSignal(int, str)
    extraction_complete = pyqtSignal(dict)

    def __init__(self, file_path):
        super().__init__()
        self.file_path = file_path

    def run(self):
        try:
            self.update_progress.emit(10, "Preprocessing document...")
            preprocessed_text = preprocess_document(self.file_path)
            
            self.update_progress.emit(40, "Extracting information...")
            extracted_info = extract_information(preprocessed_text)
            
            self.update_progress.emit(70, "Validating extracted information...")
            validated_info = validate_extracted_info(extracted_info)
            
            self.update_progress.emit(100, "Extraction complete!")
            self.extraction_complete.emit(validated_info)
        except Exception as e:
            logger.error(f"Error during extraction: {str(e)}")
            self.update_progress.emit(100, f"Error: {str(e)}")

class BillExtractorGUI(QWidget):
    def __init__(self):
        super().__init__()
        self.initUI()

    def initUI(self):
        layout = QVBoxLayout()

        self.file_label = QLabel('No file selected', self)
        layout.addWidget(self.file_label)

        self.select_button = QPushButton('Select Bill', self)
        self.select_button.clicked.connect(self.select_file)
        layout.addWidget(self.select_button)

        self.extract_button = QPushButton('Extract Information', self)
        self.extract_button.clicked.connect(self.extract_info)
        layout.addWidget(self.extract_button)

        self.output_format = QComboBox(self)
        self.output_format.addItems(['Text', 'JSON', 'CSV'])
        layout.addWidget(self.output_format)

        self.progress_bar = QProgressBar(self)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel('', self)
        layout.addWidget(self.status_label)

        self.result_text = QTextEdit(self)
        self.result_text.setReadOnly(True)
        layout.addWidget(self.result_text)

        self.setLayout(layout)
        self.setWindowTitle('Bill Information Extractor')
        self.setGeometry(300, 300, 500, 400)

    def select_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Bill File", "", "All Files (*);;PDF Files (*.pdf);;Image Files (*.png *.jpg *.jpeg)")
        if file_path:
            self.file_label.setText(f'Selected file: {file_path}')
            self.file_path = file_path

    def extract_info(self):
        if hasattr(self, 'file_path'):
            self.extraction_thread = ExtractionThread(self.file_path)
            self.extraction_thread.update_progress.connect(self.update_progress)
            self.extraction_thread.extraction_complete.connect(self.display_results)
            self.extraction_thread.start()
            self.extract_button.setEnabled(False)
        else:
            self.result_text.setPlainText("Please select a file first.")

    def update_progress(self, value, status):
        self.progress_bar.setValue(value)
        self.status_label.setText(status)

    def display_results(self, validated_info):
        output_format = self.output_format.currentText()
        if output_format == 'Text':
            output = '\n'.join([f"{k.replace('_', ' ').title()}: {v}" for k, v in validated_info.items()])
        elif output_format == 'JSON':
            output = json.dumps(validated_info, indent=2)
        elif output_format == 'CSV':
            output = 'Key,Value\n'
            output += '\n'.join([f"{k},{v}" for k, v in validated_info.items()])

        self.result_text.setPlainText(output)
        self.extract_button.setEnabled(True)

def main():
    app = QApplication(sys.argv)
    ex = BillExtractorGUI()
    ex.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
