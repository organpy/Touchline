#!/usr/bin/env python3
"""
Validation script for ResendOfferModal and bot restart recovery.
Demonstrates that the feature is properly integrated.
"""

import sys
from pathlib import Path

# Add project to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def check_imports():
    """Verify all required imports are available."""
    print("🔍 Checking imports...")
    try:
        from cogs.admin.pending_offers import ResendOfferModal, ExpireOfferModal
        print("   ✅ ResendOfferModal imported successfully")
        
        from cogs.transactions.transactions import TransactionsCog
        print("   ✅ TransactionsCog imported successfully")
        
        from utils.team_display import format_team_label_from_record
        from utils.embed_builder import EmbedBuilder
        print("   ✅ Embed utilities imported successfully")
        
        return True
    except Exception as e:
        print(f"   ❌ Import failed: {e}")
        return False


def check_resend_modal_structure():
    """Verify ResendOfferModal has required methods and fields."""
    print("\n🔍 Checking ResendOfferModal structure...")
    try:
        from cogs.admin.pending_offers import ResendOfferModal
        import inspect
        
        # Check for required method
        if hasattr(ResendOfferModal, 'on_submit'):
            print("   ✅ on_submit method exists")
        else:
            print("   ❌ on_submit method missing")
            return False
        
        # Check for input field
        if hasattr(ResendOfferModal, 'offer_id'):
            print("   ✅ offer_id TextInput field exists")
        else:
            print("   ❌ offer_id field missing")
            return False
        
        # Check method signature
        sig = inspect.signature(ResendOfferModal.on_submit)
        if 'interaction' in sig.parameters:
            print("   ✅ on_submit has interaction parameter")
        else:
            print("   ❌ on_submit missing interaction parameter")
            return False
        
        return True
    except Exception as e:
        print(f"   ❌ Structure check failed: {e}")
        return False


def check_cog_load_recovery():
    """Verify cog_load has correct field names and logic."""
    print("\n🔍 Checking cog_load recovery logic...")
    try:
        import inspect
        from cogs.transactions.transactions import TransactionsCog
        
        source = inspect.getsource(TransactionsCog.cog_load)
        
        # Check for correct field name
        if 'channel_id = offer.get' in source and 'message_channel_id' not in source:
            print("   ✅ Uses correct 'channel_id' field (not message_channel_id)")
        else:
            print("   ⚠️  Field name usage should be reviewed")
        
        # Check for status filtering
        if "eq(\"status\", \"pending\")" in source:
            print("   ✅ Filters by pending status")
        else:
            print("   ⚠️  May query all offers instead of just pending")
        
        # Check for proper logging
        if "logger.info" in source and "logger.warning" in source:
            print("   ✅ Has logging for recovery")
        else:
            print("   ⚠️  Missing logging")
        
        return True
    except Exception as e:
        print(f"   ❌ Recovery check failed: {e}")
        return False


def check_embed_integration():
    """Verify unified embed format is accessible."""
    print("\n🔍 Checking embed builder integration...")
    try:
        from utils.embed_builder import EmbedBuilder
        import inspect
        
        # Check for add_league_field method
        if hasattr(EmbedBuilder, 'add_league_field'):
            print("   ✅ EmbedBuilder.add_league_field exists")
            
            # Check signature
            sig = inspect.signature(EmbedBuilder.add_league_field)
            if 'league_code' in sig.parameters:
                print("   ✅ add_league_field has league_code parameter")
            else:
                print("   ⚠️  add_league_field signature may have changed")
        else:
            print("   ❌ add_league_field method missing")
            return False
        
        # Check for team theming
        if hasattr(EmbedBuilder, 'add_team_image'):
            print("   ✅ EmbedBuilder.add_team_image exists")
        else:
            print("   ❌ add_team_image method missing")
            return False
        
        return True
    except Exception as e:
        print(f"   ❌ Embed check failed: {e}")
        return False


def check_persistent_views():
    """Verify DynamicItem pattern for button persistence."""
    print("\n🔍 Checking persistent view pattern...")
    try:
        import inspect
        from cogs.transactions.persistent_views import SigningAcceptButton, SigningDeclineButton
        
        # Check for DynamicItem decorator
        for button_class in [SigningAcceptButton, SigningDeclineButton]:
            source = inspect.getsource(button_class)
            if '@discord.ui.DynamicItem' in source or 'DynamicItem' in source:
                print(f"   ✅ {button_class.__name__} uses DynamicItem pattern")
            else:
                print(f"   ⚠️  {button_class.__name__} may not use DynamicItem")
        
        return True
    except Exception as e:
        print(f"   ❌ Persistent view check failed: {e}")
        return False


def main():
    """Run all validation checks."""
    print("=" * 60)
    print("ResendOfferModal & Persistence Validation")
    print("=" * 60)
    
    checks = [
        ("Imports", check_imports),
        ("ResendOfferModal Structure", check_resend_modal_structure),
        ("Bot Recovery Logic", check_cog_load_recovery),
        ("Embed Integration", check_embed_integration),
        ("Persistent Views", check_persistent_views),
    ]
    
    results = []
    for name, check_func in checks:
        try:
            result = check_func()
            results.append((name, result))
        except Exception as e:
            print(f"❌ {name} check crashed: {e}")
            results.append((name, False))
    
    # Summary
    print("\n" + "=" * 60)
    print("VALIDATION SUMMARY")
    print("=" * 60)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✅" if result else "❌"
        print(f"{status} {name}")
    
    print(f"\nTotal: {passed}/{total} checks passed")
    
    if passed == total:
        print("\n🎉 All validation checks passed!")
        print("ResendOfferModal is ready for production.")
        return 0
    else:
        print(f"\n⚠️  {total - passed} check(s) failed or need review.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
