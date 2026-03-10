# Icons and Images

## Required Images

This module requires the following images to be added:

### 1. Module Icon
- **Path**: `static/description/icon.png`
- **Size**: 256x256 pixels
- **Format**: PNG
- **Purpose**: Displayed in Odoo Apps menu
- **Recommendation**: Use Affirm's official branding or a generic payment icon

### 2. Affirm Logo
- **Path**: `static/src/img/affirm_logo.png`
- **Size**: Approximately 100-200px wide
- **Format**: PNG (with transparency)
- **Purpose**: Displayed on payment selection page
- **Source**: Download from Affirm's brand assets or use official logo

## Getting Official Affirm Branding

1. Visit Affirm's brand guidelines page
2. Download official logos in appropriate sizes
3. Follow Affirm's brand usage guidelines
4. Ensure proper attribution

## Temporary Placeholder

For development purposes, you can use a generic payment icon until official branding is obtained.

### Creating a Simple Icon

Using ImageMagick (if available):

```bash
# Create a simple blue square with "A" text as placeholder
convert -size 256x256 xc:"#0FA0EA" -gravity center -pointsize 180 -fill white -annotate +0+0 "A" static/description/icon.png

# Create Affirm logo placeholder
convert -size 200x50 xc:"#0FA0EA" -gravity center -pointsize 36 -fill white -annotate +0+0 "affirm" static/src/img/affirm_logo.png
```

### Alternative: Online Icon Generators

If ImageMagick is not available:
1. Use https://www.canva.com or similar
2. Create 256x256px image with blue background (#0FA0EA)
3. Add white text "Affirm" or "A"
4. Export as PNG
5. Save to appropriate directory

## Brand Colors

Official Affirm colors:
- **Primary Blue**: #0FA0EA
- **Dark Blue**: #0A3A5C
- **White**: #FFFFFF

## Usage Guidelines

When using official Affirm branding:
- Follow Affirm's brand guidelines
- Maintain proper spacing and sizing
- Don't modify or distort logos
- Use official color schemes
- Include proper attribution if required

## Notes

The module will function without custom icons, but adding appropriate branding improves the professional appearance and user trust.
