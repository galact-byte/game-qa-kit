draw_set_color(make_color_rgb(48, 72, 88));
draw_rectangle(0, 0, 640, 480, false);
draw_set_color(c_white);
draw_text(24, 24, "GameMaker authored runtime probe");
draw_text(24, 52, "Step " + string(probe_step) + " / x " + string(x));
draw_text(24, 80, "Right " + string(probe_right) + " / focus " + string(probe_focus));
for (var bit_index = 0; bit_index < 16; bit_index++) {
    draw_set_color(((probe_step >> bit_index) & 1) ? c_yellow : c_aqua);
    draw_rectangle(24 + bit_index * 36, 120, 52 + bit_index * 36, 148, false);
}
draw_set_color(c_lime);
draw_rectangle(x, y, x + 32, y + 32, false);
