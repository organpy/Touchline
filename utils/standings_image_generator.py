"""
Generate standings table as an image using Pillow.
Provides a visual, formatted standings table for Discord embeds.
"""

import logging
from typing import List, Dict, Any, Optional
from PIL import Image, ImageDraw, ImageFont
import io

logger = logging.getLogger(__name__)

# Colors (RGB tuples)
COLOR_HEADER = (52, 152, 219)  # Blue
COLOR_ROW_ODD = (236, 240, 241)  # Light gray
COLOR_ROW_EVEN = (255, 255, 255)  # White
COLOR_TEXT = (44, 62, 80)  # Dark text
COLOR_TEXT_HEADER = (255, 255, 255)  # White text
COLOR_BORDER = (189, 195, 199)  # Gray border
COLOR_RANK_1 = (241, 196, 15)  # Gold
COLOR_RANK_2 = (192, 192, 192)  # Silver
COLOR_RANK_3 = (205, 127, 50)  # Bronze

# Dimensions
PADDING = 10
LINE_HEIGHT = 32
HEADER_HEIGHT = 40
CELL_PADDING = 8


def get_rank_color(rank: int) -> tuple:
    """Get special color for top 3 teams."""
    if rank == 1:
        return COLOR_RANK_1
    elif rank == 2:
        return COLOR_RANK_2
    elif rank == 3:
        return COLOR_RANK_3
    return COLOR_ROW_EVEN if rank % 2 == 0 else COLOR_ROW_ODD


def generate_standings_image(
    standings_data: List[Dict[str, Any]],
    season_name: str,
    league_name: str,
) -> Optional[io.BytesIO]:
    """
    Generate a standings table image using Pillow.
    
    Args:
        standings_data: List of team standings dictionaries
        season_name: Season name (e.g., "S10")
        league_name: League name (e.g., "NBL Prime")
    
    Returns:
        BytesIO buffer of PNG image, or None on error
    """
    try:
        # Column widths and positions
        col_rank = 30
        col_team = 180
        col_w = 35
        col_l = 35
        col_d = 35
        col_pf = 50
        col_pa = 50
        col_diff = 55

        total_width = (
            PADDING * 2 + col_rank + col_team + col_w + col_l + col_d + col_pf + col_pa + col_diff
        )
        total_height = PADDING * 2 + HEADER_HEIGHT + (len(standings_data) * LINE_HEIGHT) + 40

        # Create image
        img = Image.new("RGB", (total_width, total_height), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)

        # Try to load a nice font, fall back to default if not available
        try:
            title_font = ImageFont.truetype("arial.ttf", 20)
            header_font = ImageFont.truetype("arial.ttf", 14)
            body_font = ImageFont.truetype("arial.ttf", 12)
        except:
            title_font = ImageFont.load_default()
            header_font = ImageFont.load_default()
            body_font = ImageFont.load_default()

        y = PADDING

        # Title
        title = f"{league_name} - {season_name} Standings"
        draw.text((PADDING, y), title, fill=COLOR_TEXT, font=title_font)
        y += 35

        # Header row
        header_y = y
        draw.rectangle(
            [(PADDING, header_y), (total_width - PADDING, header_y + HEADER_HEIGHT)],
            fill=COLOR_HEADER,
        )

        x = PADDING + CELL_PADDING
        headers = [
            ("Rk", col_rank),
            ("Team", col_team),
            ("W", col_w),
            ("L", col_l),
            ("D", col_d),
            ("PF", col_pf),
            ("PA", col_pa),
            ("Diff", col_diff),
        ]

        for header_text, col_width in headers:
            draw.text((x, header_y + CELL_PADDING), header_text, fill=COLOR_TEXT_HEADER, font=header_font)
            x += col_width

        y += HEADER_HEIGHT

        # Data rows
        for rank, team in enumerate(standings_data, 1):
            row_y = y
            row_color = get_rank_color(rank)

            # Draw row background
            draw.rectangle(
                [(PADDING, row_y), (total_width - PADDING, row_y + LINE_HEIGHT)],
                fill=row_color,
            )

            # Draw row border
            draw.rectangle(
                [(PADDING, row_y), (total_width - PADDING, row_y + LINE_HEIGHT)],
                outline=COLOR_BORDER,
                width=1,
            )

            # Prepare cell values
            cells = [
                (str(rank), col_rank),
                (team.get("team_name", "Unknown")[:15], col_team),
                (str(team.get("wins", 0)), col_w),
                (str(team.get("losses", 0)), col_l),
                (str(team.get("draws", 0)), col_d),
                (str(team.get("points_for", 0)), col_pf),
                (str(team.get("points_against", 0)), col_pa),
                (str(team.get("points_diff", 0)), col_diff),
            ]

            # Draw cells
            x = PADDING + CELL_PADDING
            for cell_value, col_width in cells:
                draw.text((x, row_y + CELL_PADDING), cell_value, fill=COLOR_TEXT, font=body_font)
                x += col_width

            y += LINE_HEIGHT

        # Legend at bottom
        legend_y = y + 10
        legend_text = "🥇 1st  🥈 2nd  🥉 3rd  (Highlighted above)"
        draw.text((PADDING, legend_y), legend_text, fill=COLOR_TEXT, font=body_font)

        # Save to BytesIO
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)
        return buffer

    except Exception as exc:
        logger.error(f"Failed to generate standings image: {exc}", exc_info=True)
        return None
