#!/usr/bin/env python3
import json
import sys
import os
import argparse
from collections import defaultdict
from olmocr.data.renderpdf import render_pdf_to_base64png
from runners.run_gemini import run_gemini
from runners.run_chatgpt import run_chatgpt
from runners.run_difference import run_difference

def parse_rules_file(file_path):
    """Parse the rules file and organize rules by PDF."""
    pdf_rules = defaultdict(list)
    
    with open(file_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            
            try:
                rule = json.loads(line)
                if 'pdf' in rule:
                    pdf_rules[rule['pdf']].append(rule)
            except json.JSONDecodeError:
                print(f"Warning: Could not parse line as JSON: {line}")
    
    return pdf_rules

def get_model_outputs(pdf_path):
    """Get outputs from both models for a given PDF."""
    try:
        chatgpt_output = run_chatgpt(pdf_path)
        gemini_output = run_gemini(pdf_path)
        return chatgpt_output, gemini_output
    except Exception as e:
        print(f"Error getting model outputs for {pdf_path}: {str(e)}")
        return f"Error: {str(e)}", f"Error: {str(e)}"

def generate_html(pdf_rules, rules_file_path, pdfs_to_process=None):
    """Generate the HTML page with PDF renderings and model outputs."""
    # Use provided PDFs or limit to 10 unique PDFs from rules
    if pdfs_to_process:
        pdf_names = pdfs_to_process
    else:
        pdf_names = list(pdf_rules.keys())[:10]
    
    html = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>PDF Model Comparison</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                margin: 0;
                padding: 20px;
                background-color: #f5f5f5;
            }
            
            .container {
                max-width: 1800px;
                margin: 0 auto;
            }
            
            h1 {
                color: #333;
                text-align: center;
                margin-bottom: 30px;
            }
            
            .pdf-container {
                background-color: white;
                border-radius: 8px;
                box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
                margin-bottom: 30px;
                overflow: hidden;
            }
            
            .pdf-header {
                background-color: #4a6fa5;
                color: white;
                padding: 15px;
                font-size: 18px;
                font-weight: bold;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }
            
            .pdf-content {
                display: flex;
                flex-direction: row;
                padding: 20px;
            }
            
            @media (max-width: 1200px) {
                .pdf-content {
                    flex-direction: column;
                }
            }
            
            .pdf-image {
                flex: 0 0 30%;
                max-width: 500px;
                text-align: center;
                padding-right: 20px;
            }
            
            .pdf-image img {
                max-width: 100%;
                height: auto;
                border: 1px solid #ddd;
            }
            
            .models-container {
                flex: 1;
                display: flex;
                flex-direction: column;
            }
            
            .model-outputs {
                display: flex;
                flex-direction: row;
                margin-bottom: 20px;
            }
            
            .model-output {
                flex: 1;
                margin: 0 10px;
                border: 1px solid #ddd;
                border-radius: 5px;
                overflow: hidden;
            }
            
            .model-header {
                background-color: #4a6fa5;
                color: white;
                padding: 10px;
                font-weight: bold;
                text-align: center;
            }
            
            .model-content {
                padding: 15px;
                height: 400px;
                overflow-y: auto;
                white-space: pre-wrap;
                font-family: monospace;
                font-size: 14px;
                background-color: #f8f9fa;
            }
            
            .difference-content {
                padding: 15px;
                height: 200px;
                overflow-y: auto;
                white-space: pre-wrap;
                font-family: monospace;
                font-size: 14px;
                background-color: #f8f9fa;
                display: none;
                margin-top: 20px;
                border: 1px solid #ddd;
                border-radius: 5px;
            }
            
            .controls {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 20px;
                padding: 10px;
                background-color: #e9ecef;
                border-radius: 5px;
            }
            
            .rating-controls {
                display: flex;
                flex-wrap: wrap;
                gap: 10px;
            }
            
            button {
                padding: 8px 15px;
                border: none;
                border-radius: 4px;
                cursor: pointer;
                font-weight: bold;
                transition: background-color 0.2s;
            }
            
            button:hover {
                opacity: 0.9;
            }
            
            .rating-btn {
                flex: 1;
                min-width: 120px;
            }
            
            .chatgpt-better {
                background-color: #28a745;
                color: white;
            }
            
            .gemini-better {
                background-color: #dc3545;
                color: white;
            }
            
            .both-good {
                background-color: #17a2b8;
                color: white;
            }
            
            .both-bad {
                background-color: #6c757d;
                color: white;
            }
            
            .invalid-pdf {
                background-color: #343a40;
                color: white;
            }
            
            .show-diff-btn {
                background-color: #fd7e14;
                color: white;
            }
            
            .highlight {
                background-color: #ffff99;
            }
            
            .rating-indicator {
                display: inline-block;
                padding: 5px 10px;
                border-radius: 4px;
                color: white;
                font-weight: bold;
                margin-left: 10px;
            }
            
            .error {
                color: #dc3545;
                padding: 20px;
                text-align: center;
                border: 1px solid #dc3545;
                border-radius: 5px;
                margin: 20px;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>PDF Model Comparison</h1>
    """
    
    pdf_bases = os.path.join(os.path.dirname(rules_file_path), 'pdfs')
    
    for pdf_name in pdf_names:
        pdf_path = os.path.join(pdf_bases, pdf_name)
        
        # Render the PDF (first page only)
        try:
            base64_img = render_pdf_to_base64png(pdf_path, 0)
            img_html = f'<img src="data:image/png;base64,{base64_img}" alt="{pdf_name}">'
        except Exception as e:
            img_html = f'<div class="error">Error rendering PDF: {str(e)}</div>'
        
        # Get model outputs
        chatgpt_output, gemini_output = get_model_outputs(pdf_path)
        
        html += f"""
        <div class="pdf-container" id="pdf-{pdf_name.replace('.', '-')}">
            <div class="pdf-header">
                <span>{pdf_name}</span>
                <span class="rating-indicator" id="rating-{pdf_name.replace('.', '-')}"></span>
            </div>
            <div class="pdf-content">
                <div class="pdf-image">
                    {img_html}
                </div>
                <div class="models-container">
                    <div class="controls">
                        <div class="rating-controls">
                            <button class="rating-btn chatgpt-better" onclick="rateModel('{pdf_name.replace('.', '-')}', 'chatgpt')">ChatGPT Better</button>
                            <button class="rating-btn gemini-better" onclick="rateModel('{pdf_name.replace('.', '-')}', 'gemini')">Gemini Better</button>
                            <button class="rating-btn both-good" onclick="rateModel('{pdf_name.replace('.', '-')}', 'both-good')">Both Good</button>
                            <button class="rating-btn both-bad" onclick="rateModel('{pdf_name.replace('.', '-')}', 'both-bad')">Both Bad</button>
                            <button class="rating-btn invalid-pdf" onclick="rateModel('{pdf_name.replace('.', '-')}', 'invalid')">Invalid PDF</button>
                        </div>
                        <button class="show-diff-btn" onclick="toggleDifference('{pdf_name.replace('.', '-')}')">Show Differences</button>
                    </div>
                    <div class="model-outputs">
                        <div class="model-output">
                            <div class="model-header">ChatGPT Output</div>
                            <div class="model-content" id="chatgpt-{pdf_name.replace('.', '-')}">{chatgpt_output}</div>
                        </div>
                        <div class="model-output">
                            <div class="model-header">Gemini Output</div>
                            <div class="model-content" id="gemini-{pdf_name.replace('.', '-')}">{gemini_output}</div>
                        </div>
                    </div>
                    <div class="difference-content" id="diff-{pdf_name.replace('.', '-')}">
                        <h3>Differences</h3>
                        <p>Loading differences...</p>
                    </div>
                </div>
            </div>
        </div>
        """
    
    html += """
        </div>
        
        <script>
            // Store ratings
            const ratings = {};
            
            function rateModel(pdfId, rating) {
                // Save rating
                ratings[pdfId] = rating;
                
                // Update visual indicator
                const indicator = document.getElementById(`rating-${pdfId}`);
                indicator.textContent = rating.replace('-', ' ').toUpperCase();
                
                // Apply class matching the rating
                indicator.className = 'rating-indicator ' + rating;
                
                // Save ratings to localStorage
                localStorage.setItem('pdf-model-ratings', JSON.stringify(ratings));
            }
            
            function toggleDifference(pdfId) {
                const diffElement = document.getElementById(`diff-${pdfId}`);
                const chatgptContent = document.getElementById(`chatgpt-${pdfId}`).textContent;
                const geminiContent = document.getElementById(`gemini-${pdfId}`).textContent;
                
                if (diffElement.style.display === 'none' || !diffElement.style.display) {
                    diffElement.style.display = 'block';
                    
                    // If content is "Loading differences...", calculate differences
                    if (diffElement.textContent.includes("Loading differences...")) {
                        // Simple difference highlighting
                        const diffResult = findDifferences(chatgptContent, geminiContent);
                        diffElement.innerHTML = diffResult;
                    }
                } else {
                    diffElement.style.display = 'none';
                }
            }
            
            function findDifferences(text1, text2) {
                // Split into sentences for comparison
                const sentences1 = text1.split(/(?<=[.!?])\\s+/);
                const sentences2 = text2.split(/(?<=[.!?])\\s+/);
                
                let result = "<h4>ChatGPT unique content:</h4><ul>";
                
                // Find sentences in text1 that aren't in text2
                sentences1.forEach(sentence => {
                    if (sentence.trim().length > 10 && !text2.includes(sentence)) {
                        result += `<li><span class="highlight">${sentence}</span></li>`;
                    }
                });
                
                result += "</ul><h4>Gemini unique content:</h4><ul>";
                
                // Find sentences in text2 that aren't in text1
                sentences2.forEach(sentence => {
                    if (sentence.trim().length > 10 && !text1.includes(sentence)) {
                        result += `<li><span class="highlight">${sentence}</span></li>`;
                    }
                });
                
                result += "</ul>";
                return result;
            }
            
            // Load saved ratings on page load
            window.onload = function() {
                const savedRatings = localStorage.getItem('pdf-model-ratings');
                if (savedRatings) {
                    const parsedRatings = JSON.parse(savedRatings);
                    for (const pdfId in parsedRatings) {
                        rateModel(pdfId, parsedRatings[pdfId]);
                    }
                }
            };
        </script>
    </body>
    </html>
    """
    
    return html

def main():
    parser = argparse.ArgumentParser(description='Generate an HTML visualization for comparing AI model outputs on PDFs.')
    parser.add_argument('-r', '--rules_file', help='Rules file path', default='./sample_data/pdfs/discoverworld_crazy_table4.pdf')
    # parser.add_argument('rules_file', help='Path to the rules file (JSON lines format)')
    parser.add_argument('-o', '--output', help='Output HTML file path', default='pdf_model_comparison.html')
    parser.add_argument('-p', '--pdfs', nargs='+', help='Specific PDF files to process')
    
    args = parser.parse_args()
    
    if not os.path.exists(args.rules_file):
        print(f"Error: Rules file not found: {args.rules_file}")
        sys.exit(1)
    
    pdf_rules = parse_rules_file(args.rules_file)
    html = generate_html(pdf_rules, args.rules_file, args.pdfs)
    
    with open(args.output, 'w') as f:
        f.write(html)
    
    print(f"HTML visualization created: {args.output}")
    print("Open this file in a web browser to view and rate model outputs.")

if __name__ == "__main__":
    main()