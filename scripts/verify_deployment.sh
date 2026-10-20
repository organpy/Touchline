#!/bin/bash
# Touchline Pre-Deployment Verification Script
# Run this before deploying to production

set -e  # Exit on any error

echo "🚀 Touchline Pre-Deployment Verification"
echo "======================================"
echo ""

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

ERRORS=0
WARNINGS=0

# Function to print status
print_status() {
    if [ $1 -eq 0 ]; then
        echo -e "${GREEN}✅ $2${NC}"
    else
        echo -e "${RED}❌ $2${NC}"
        ((ERRORS++))
    fi
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
    ((WARNINGS++))
}

# 1. Check Python version
echo "1. Checking Python version..."
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
if python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"; then
    print_status 0 "Python version: $PYTHON_VERSION"
else
    print_status 1 "Python 3.10+ required, found: $PYTHON_VERSION"
fi

# 2. Check if .env exists
echo ""
echo "2. Checking environment configuration..."
if [ -f ".env" ]; then
    print_status 0 ".env file exists"
    
    # Check required variables
    required_vars=("DISCORD_TOKEN" "APPLICATION_ID" "SUPABASE_URL" "SUPABASE_KEY")
    for var in "${required_vars[@]}"; do
        if grep -q "^${var}=" .env && ! grep -q "^${var}=your_" .env; then
            print_status 0 "$var is set"
        else
            print_status 1 "$var not configured in .env"
        fi
    done
    
    # Check .env permissions
    PERMS=$(stat -c %a .env 2>/dev/null || stat -f %A .env 2>/dev/null || echo "unknown")
    if [ "$PERMS" = "600" ]; then
        print_status 0 ".env permissions: $PERMS (secure)"
    else
        print_warning ".env permissions: $PERMS (should be 600)"
    fi
else
    print_status 1 ".env file missing (copy from .env.sample)"
fi

# 3. Check virtual environment
echo ""
echo "3. Checking virtual environment..."
if [ -d ".venv" ]; then
    print_status 0 "Virtual environment exists"
else
    print_warning "Virtual environment not found (.venv)"
fi

# 4. Check dependencies
echo ""
echo "4. Checking dependencies..."
if [ -f "requirements.txt" ]; then
    print_status 0 "requirements.txt exists"
    
    # Try to import key packages
    if python3 -c "import discord" 2>/dev/null; then
        print_status 0 "discord.py installed"
    else
        print_status 1 "discord.py not installed"
    fi
    
    if python3 -c "import supabase" 2>/dev/null; then
        print_status 0 "supabase installed"
    else
        print_status 1 "supabase not installed"
    fi
else
    print_status 1 "requirements.txt missing"
fi

# 5. Check critical files
echo ""
echo "5. Checking critical files..."
critical_files=("bot.py" "utils/health_check.py" "utils/rate_limiter.py" "db/queries.py")
for file in "${critical_files[@]}"; do
    if [ -f "$file" ]; then
        print_status 0 "$file exists"
    else
        print_status 1 "$file missing"
    fi
done

# 6. Check for secrets in git
echo ""
echo "6. Checking git security..."
if [ -d ".git" ]; then
    if git check-ignore .env >/dev/null 2>&1; then
        print_status 0 ".env is in .gitignore"
    else
        print_warning ".env not in .gitignore"
    fi
    
    if git log --all --full-history -- .env 2>/dev/null | grep -q "commit"; then
        print_status 1 ".env found in git history (SECURITY RISK!)"
    else
        print_status 0 ".env not in git history"
    fi
else
    print_warning "Not a git repository"
fi

# 7. Syntax check
echo ""
echo "7. Checking Python syntax..."
if python3 -m py_compile bot.py 2>/dev/null; then
    print_status 0 "bot.py syntax valid"
else
    print_status 1 "bot.py has syntax errors"
fi

# 8. Run tests
echo ""
echo "8. Running tests..."
if command -v pytest &> /dev/null; then
    if pytest test/ -q --tb=no 2>/dev/null; then
        print_status 0 "All tests passing"
    else
        print_status 1 "Some tests failing (run: pytest test/ -v)"
    fi
else
    print_warning "pytest not installed (run: pip install pytest)"
fi

# 9. Check documentation
echo ""
echo "9. Checking documentation..."
docs=("docs/ORACLE_CLOUD_DEPLOYMENT.md" "docs/PRODUCTION_CHECKLIST.md" "docs/PRODUCTION_READINESS.md")
for doc in "${docs[@]}"; do
    if [ -f "$doc" ]; then
        print_status 0 "$doc exists"
    else
        print_warning "$doc missing"
    fi
done

# Summary
echo ""
echo "======================================"
echo "📊 Verification Summary"
echo "======================================"
echo ""

if [ $ERRORS -eq 0 ] && [ $WARNINGS -eq 0 ]; then
    echo -e "${GREEN}🎉 All checks passed! Ready for production.${NC}"
    echo ""
    echo "Next steps:"
    echo "1. Review docs/PRODUCTION_CHECKLIST.md"
    echo "2. Follow docs/ORACLE_CLOUD_DEPLOYMENT.md"
    echo "3. Test health check: curl http://localhost:8080/health"
    exit 0
elif [ $ERRORS -eq 0 ]; then
    echo -e "${YELLOW}⚠️  $WARNINGS warning(s) found. Review before deploying.${NC}"
    echo ""
    echo "You can proceed, but address warnings if possible."
    exit 0
else
    echo -e "${RED}❌ $ERRORS error(s) and $WARNINGS warning(s) found.${NC}"
    echo ""
    echo "Fix errors before deploying to production."
    echo ""
    echo "Common fixes:"
    echo "- Copy .env.sample to .env and configure"
    echo "- Run: pip install -r requirements.txt"
    echo "- Run: pytest test/ -v"
    exit 1
fi
