"""Utility functions for formatting and display."""


def number_to_ordinal(n: int) -> str:
    """
    Convert a number to its ordinal representation.
    
    Examples:
        1 -> "1st"
        2 -> "2nd"
        3 -> "3rd"
        4 -> "4th"
        11 -> "11th"
        21 -> "21st"
        22 -> "22nd"
        23 -> "23rd"
    """
    if not isinstance(n, int) or n < 1:
        return str(n)
    
    # Handle 11, 12, 13 specially (they use "th")
    if 10 <= n % 100 <= 13:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    
    return f"{n}{suffix}"
