"""
Generate standings table as an image using Pillow.
Provides a modern, visual standings table with team logos/emojis.
"""

import logging
from typing import List, Dict, Any, Optional
from PIL import Image, ImageDraw, ImageFont
import io

logger = logging.getLogger(__name__)

# Modern color palette
COLOR_BG = (245, 247, 250)  # Light blue-gray background
COLOR_HEADER_BG = (31, 78, 121)  # Dark blue header
COLOR_HEADER_TEXT = (255, 255, 255)  # White header text
COLOR_TEXT = (33, 47, 61)  # Dark text
COLOR_TEXT_SECONDARY = (102, 126, 149)  # Secondary text (stats)
COLOR_BORDER = (200, 213, 230)  # Light border
COLOR_RANK_1 = (255, 193, 7)  # Gold for 1st
COLOR_RANK_2 = (229, 229, 229)  # Silver for 2nd
COLOR_RANK_3 = (205, 127, 50)  # Bronze for 3rd
COLOR_ROW_ALT = (255, 255, 255)  # White rows
COLOR_DIVIDER = (230, 240, 250)  # Subtle divider

# Dimensions
PADDING_H = 16
PADDING_V = 12
ROW_HEIGHT = 40
HEADER_HEIGHT = 42
EMOJI_SIZE = 24
TITLE_MARGIN = 20

# Font sizes
TITLE_SIZE = 22
HEADER_SIZE = 13
BODY_SIZE = 12
EMOJI_SIZE_PX = 18


def get_rank_color(rank: int) -> tuple:
    """Get special color for top 3 teams."""
    if rank == 1:
        return COLOR_RANK_1
    elif rank == 2:
        return COLOR_RANK_2
    elif rank == 3:
        return COLOR_RANK_3
    # Alternate row colors for better readability
    return COLOR_ROW_ALT


def generate_standings_image(
    standings_data: List[Dict[str, Any]],
    season_name: str,
    league_name: str,
) -> Optional[io.BytesIO]:
    """
    Generate a modern standings table image using Pillow.
    
    Args:
        standings_data: List of team standings dictionaries with fields:
            - name: Team name
            - emoji: Team emoji
            - wins, losses, draws: W-L-D record
            - points_for, points_against: PF-PA
            - point_diff: Difference
        season_name: Season name (e.g., "S10")
        league_name: League name (e.g., "NBL Prime")
    
    Returns:
        BytesIO buffer of PNG image, or None on error
    """
    try:
        if not standings_data:
            return None

        # Column widths (adjusted for team emoji display)
        col_rk = 40
        col_emoji = 35
        col_team = 150
        col_w = 40
        col_l = 40
        col_pf = 45
        col_pa = 45
        col_diff = 50

        total_width = (
            PADDING_H * 2 + col_rk + col_emoji + col_team + col_w + col_l + col_pf + col_pa + col_diff
        )
        title_height = TITLE_MARGIN + 30
        total_height = (
            PADDING_V * 2 + title_height + HEADER_HEIGHT + (len(standings_data) * ROW_HEIGHT) + 20
        )

        # Create image with background
        img = Image.new("RGB", (total_width, total_height), color=COLOR_BG)
        draw = ImageDraw.Draw(img)

        # Load fonts with fallback
        try:
            title_font = ImageFont.truetype("arial.ttf", TITLE_SIZE)
            header_font = ImageFont.truetype("arial.ttf", HEADER_SIZE)
            body_font = ImageFont.truetype("arial.ttf", BODY_SIZE)
        except:
            title_font = ImageFont.load_default()
            header_font = ImageFont.load_default()
            body_font = ImageFont.load_default()

        y = PADDING_V

        # Title with season and league
        title = f"🏆 {league_name} — {season_name}"
        draw.text((PADDING_H, y), title, fill=COLOR_TEXT, font=title_font)
        y += title_height

        # Header row with gradient-like effect
        header_y = y
        draw.rectangle(
            [(0, header_y), (total_width, header_y + HEADER_HEIGHT)],
            fill=COLOR_HEADER_BG,
        )

        # Header text
        headers = [
            ("Rk", col_rk),
            ("", col_emoji),  # Emoji column (no label)
            ("Team", col_team),
            ("W", col_w),
            ("L", col_l),
            ("PF", col_pf),
            ("PA", col_pa),
            ("Diff", col_diff),
        ]

        x = PADDING_H
        for header_text, col_width in headers:
            if header_text:
                draw.text((x + col_width // 2 - 10, header_y + HEADER_HEIGHT // 2 - 7), 
                         header_text, fill=COLOR_HEADER_TEXT, font=header_font)
            x += col_width

        y += HEADER_HEIGHT

        # Data rows
        for rank, team in enumerate(standings_data, 1):
            row_y = y
            row_color = get_rank_color(rank)

            # Draw row background
            draw.rectangle(
                [(0, row_y), (total_width, row_y + ROW_HEIGHT)],
                fill=row_color,
            )

            # Add subtle top border for top 3
            if rank <= 3:
                draw.line(
                    [(0, row_y), (total_width, row_y)],
                    fill=(255, 215, 0) if rank == 1 else (192, 192, 192) if rank == 2 else (205, 127, 50),
                    width=3,
                )

            # Draw bottom border
            draw.line(
                [(PADDING_H, row_y + ROW_HEIGHT), (total_width - PADDING_H, row_y + ROW_HEIGHT)],
                fill=COLOR_DIVIDER,
                width=1,
            )

            # Get team data
            team_name = team.get("name", "Unknown")[:14]
            team_emoji = team.get("emoji", "⚽")
            wins = str(team.get("wins", 0))
            losses = str(team.get("losses", 0))
            pf = str(team.get("points_for", 0))
            pa = str(team.get("points_against", 0))
            diff = str(team.get("point_diff", 0))

            # Draw rank with medal emoji for top 3
            x = PADDING_H
            if rank == 1:
                rank_text = "🥇"
            elif rank == 2:
                rank_text = "🥈"
            elif rank == 3:
                rank_text = "🥉"
            else:
                rank_text = str(rank).rjust(2)
            
            draw.text((x + col_rk // 2 - 8, row_y + ROW_HEIGHT // 2 - 6), 
                     rank_text, fill=COLOR_TEXT, font=body_font)
            x += col_rk

            # Draw team emoji
            draw.text((x + col_emoji // 2 - 9, row_y + ROW_HEIGHT // 2 - 10), 
                     team_emoji, fill=COLOR_TEXT, font=body_font)
            x += col_emoji

            # Draw team name
            draw.text((x + 5, row_y + ROW_HEIGHT // 2 - 6), 
                     team_name, fill=COLOR_TEXT, font=body_font)
            x += col_team

            # Draw stats (right-aligned for numbers)
            for stat in [wins, losses, pf, pa, diff]:
                col_width = col_w if stat in [wins, losses] else (col_pf if stat == pf else (col_pa if stat == pa else col_diff))
                draw.text((x + col_width - 25, row_y + ROW_HEIGHT // 2 - 6), 
                         stat, fill=COLOR_TEXT_SECONDARY, font=body_font)
                x += col_width

            y += ROW_HEIGHT

        # Footer info
        footer_y = y + 10
        footer_text = "W = Wins • L = Losses • PF = Points For • PA = Points Against • Diff = Difference"
        draw.text((PADDING_H, footer_y), footer_text, fill=COLOR_TEXT_SECONDARY, font=body_font)

        # Save to BytesIO
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)
        return buffer

    except Exception as exc:
        logger.error(f"Failed to generate standings image: {exc}", exc_info=True)
        return None
