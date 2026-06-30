def generate_streetview_variants(
    lat,
    lon,
    base_heading,
    fetch_streetview_image
):

    variants = []

    angle_configs = [

        # Base classification view (document item "a")
        ("streetview_primary", 0, 90),
        
        # Additional classification views
        ("streetview_90", 90, 90),
        ("streetview_180", 180, 90),

        # Center zooms (document items "b", "c", "d")
        # zoom1 fov=60 keeps it distinct from primary's fov=90
        ("streetview_zoom1", 0, 60),
        ("streetview_zoom2", 0, 70),
        ("streetview_zoom3", 0, 50),

        # Right 20° zooms
        ("streetview_right20_zoom1", 20, 90),
        ("streetview_right20_zoom2", 20, 70),
        ("streetview_right20_zoom3", 20, 50),

        # Left 20° zooms
        ("streetview_left20_zoom1", -20, 90),
        ("streetview_left20_zoom2", -20, 70),
        ("streetview_left20_zoom3", -20, 50),
    ]

    for image_type, angle_offset, fov in angle_configs:

        heading = (base_heading + angle_offset) % 360

        img, url = fetch_streetview_image(
            lat,
            lon,
            heading,
            fov
        )

        if img:

            variants.append(
                (image_type, img, url)
            )

            print(
                f"    {image_type} : "
                f"{round(heading,1)}° "
                f"({len(img)//1024} KB)"
            )

    return variants