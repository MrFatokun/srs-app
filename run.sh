#!/bin/bash
# SRS Standardizer — Setup & Run Script

set -e

echo ""
echo "┌─────────────────────────────────────────────┐"
echo "│     SRS Standardizer — ISO/IEC/IEEE 29148   │"
echo "└─────────────────────────────────────────────┘"
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is required but not found."
    exit 1
fi

# Create venv if not exists
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

# Activate venv
source venv/bin/activate

# Install dependencies
echo "📦 Installing dependencies..."
pip install -q -r requirements.txt

# Check for API key
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo ""
    echo "⚠️  ANTHROPIC_API_KEY is not set."
    echo "   Export it before running:"
    echo "   export ANTHROPIC_API_KEY=sk-ant-..."
    echo ""
fi

# Start server
echo ""
echo "🚀 Starting server at http://localhost:8000"
echo "   Press Ctrl+C to stop"
echo ""

uvicorn main:app --host 0.0.0.0 --port 8000 --reload
