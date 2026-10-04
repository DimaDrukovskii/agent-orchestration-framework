#!/bin/bash

echo "=== MTS Call Agent Setup ==="

# Check Python version
python_version=$(python3 --version 2>&1)
echo "Found: $python_version"

# Create venv
echo "Creating virtual environment..."
python3 -m venv venv
source venv/bin/activate

# Install dependencies
echo "Installing dependencies..."
pip install -r requirements.txt

# Setup .env
if [ ! -f .env ]; then
    echo "Creating .env from .env.example..."
    cp .env.example .env
    echo "⚠️  Update .env with your API keys!"
else
    echo ".env already exists"
fi

echo ""
echo "=== Setup Complete ==="
echo "Next steps:"
echo "1. Update .env with your API keys"
echo "2. Run: source venv/bin/activate"
echo "3. Run: uvicorn src.main:app --reload"
