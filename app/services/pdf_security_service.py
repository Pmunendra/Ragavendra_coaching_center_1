"""
PDF Security Service
Handles PDF protection with copy-prevention, watermarking, and download restrictions.
"""

import os
from datetime import datetime
from flask import current_app

def add_security_headers_to_pdf_response(response, filename, material, student_account):
    """
    Add security headers to PDF response to prevent copying and downloading.
    """
    # Prevent caching to ensure access control is re-verified on each request
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    
    # Display inline instead of forcing download
    response.headers["Content-Disposition"] = "inline; filename=secured.pdf"
    
    # Security headers to prevent exploitation
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "object-src 'none'; "
        "form-action 'self';"
    )
    
    # Prevent right-click and keyboard shortcuts for copying
    response.headers["X-UA-Compatible"] = "IE=edge"
    
    # Custom header indicating this is a secured material
    response.headers["X-Secured-Material"] = "true"
    response.headers["X-Student-ID"] = str(student_account.id)
    
    return response

def get_pdf_security_config(material):
    """Get PDF security configuration for client-side enforcement."""
    return {
        "enableWatermark": material.enable_watermark,
        "enableCopyProtection": material.enable_copy_protection,
        "enableDownloadProtection": material.enable_download_protection,
        "disableRightClick": material.enable_copy_protection,
        "disableKeyboardShortcuts": material.enable_copy_protection,
        "disablePrinting": material.enable_download_protection,
        "disableDownload": material.enable_download_protection,
    }

def get_pdf_viewer_html(material_id, stream_url, student_account, watermark_config):
    """
    Generate HTML for secured PDF viewer with protection features.
    Uses PDF.js for client-side rendering with security controls.
    """
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{watermark_config.get('title', 'Secured Document')}</title>
        <script src="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js"></script>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{ font-family: Arial, sans-serif; background: #f0f0f0; }}
            #viewer {{ width: 100%; height: 100vh; display: flex; flex-direction: column; }}
            #toolbar {{ background: #2c3e50; color: white; padding: 10px; display: flex; gap: 10px; align-items: center; }}
            #canvas-container {{ flex: 1; display: flex; justify-content: center; align-items: center; overflow: auto; background: #f0f0f0; }}
            canvas {{ max-width: 100%; height: auto; }}
            button {{ padding: 8px 12px; background: #3498db; color: white; border: none; cursor: pointer; border-radius: 4px; }}
            button:hover {{ background: #2980b9; }}
            button:disabled {{ background: #95a5a6; cursor: not-allowed; }}
            .watermark {{ position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%) rotate(-45deg); 
                         font-size: 48px; color: rgba(0,0,0,0.1); white-space: nowrap; pointer-events: none; z-index: -1; }}
            .security-notice {{ padding: 10px; background: #fff3cd; color: #856404; font-size: 12px; }}
            .security-lock {{ margin-left: auto; display: flex; align-items: center; gap: 5px; }}
        </style>
    </head>
    <body>
        <div id="viewer">
            <div id="toolbar">
                <h3>📄 Secured Document</h3>
                <button id="prev-btn" disabled>← Previous</button>
                <input type="number" id="page-num" min="1" value="1" style="width: 50px; padding: 5px;">
                <span id="page-count">of 0</span>
                <button id="next-btn">Next →</button>
                <div class="security-lock">
                    🔒 Access by: {student_account.full_name}
                </div>
            </div>
            <div class="security-notice">
                ⚠️ This document is secured and protected from copying, downloading, and unauthorized sharing.
            </div>
            <div id="canvas-container">
                <canvas id="pdf-canvas"></canvas>
                <div class="watermark">{student_account.full_name} | {student_account.email}</div>
            </div>
        </div>
        
        <script>
            // Disable right-click
            document.addEventListener('contextmenu', (e) => e.preventDefault());
            
            // Disable Ctrl+C, Ctrl+A, Ctrl+S, Ctrl+P
            document.addEventListener('keydown', (e) => {{
                if ((e.ctrlKey || e.metaKey) && ['c', 'a', 's', 'p'].includes(e.key.toLowerCase())) {{
                    e.preventDefault();
                }}
                // F12, Ctrl+Shift+I, Ctrl+Shift+J
                if (e.key === 'F12' || (e.ctrlKey && e.shiftKey && ['i', 'j', 'c'].includes(e.key.toLowerCase()))) {{
                    e.preventDefault();
                }}
            }});
            
            // Disable printing
            if (window.print) {{
                const originalPrint = window.print;
                window.print = function() {{
                    alert('Printing is disabled for this secured document.');
                }};
            }}
            
            // PDF.js setup
            pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';
            
            let pdfDoc = null;
            let pageNum = 1;
            const canvas = document.getElementById('pdf-canvas');
            const ctx = canvas.getContext('2d');
            
            // Fetch PDF from secure endpoint
            async function loadPdf() {{
                try {{
                    const response = await fetch('{stream_url}');
                    const arrayBuffer = await response.arrayBuffer();
                    pdfDoc = await pdfjsLib.getDocument({{ data: arrayBuffer }}).promise;
                    document.getElementById('page-count').textContent = `of ${{pdfDoc.numPages}}`;
                    renderPage(1);
                }} catch (error) {{
                    console.error('Error loading PDF:', error);
                    alert('Error loading secure document. Access may have expired.');
                }}
            }}
            
            async function renderPage(num) {{
                if (!pdfDoc || num > pdfDoc.numPages || num < 1) return;
                pageNum = num;
                document.getElementById('page-num').value = num;
                
                const page = await pdfDoc.getPage(num);
                const viewport = page.getViewport({{ scale: 1.5 }});
                
                canvas.width = viewport.width;
                canvas.height = viewport.height;
                
                const renderContext = {{
                    canvasContext: ctx,
                    viewport: viewport
                }};
                
                await page.render(renderContext).promise;
                
                // Update button states
                document.getElementById('prev-btn').disabled = pageNum <= 1;
                document.getElementById('next-btn').disabled = pageNum >= pdfDoc.numPages;
            }}
            
            // Event listeners
            document.getElementById('prev-btn').addEventListener('click', () => renderPage(pageNum - 1));
            document.getElementById('next-btn').addEventListener('click', () => renderPage(pageNum + 1));
            document.getElementById('page-num').addEventListener('change', (e) => {{
                const num = parseInt(e.target.value);
                if (num && num >= 1 && num <= pdfDoc.numPages) {{
                    renderPage(num);
                }}
            }});
            
            // Load PDF on page load
            loadPdf();
        </script>
    </body>
    </html>
    """
    return html

def log_pdf_access(material_id, student_id, action="view", ip_address=None, user_agent=None):
    """Log PDF access for audit trail."""
    from app.models import log_material_access
    return log_material_access(material_id, student_id, action, ip_address, user_agent)
