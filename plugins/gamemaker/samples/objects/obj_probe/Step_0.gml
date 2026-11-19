probe_step += 1;
probe_right = keyboard_check(vk_right);
probe_focus = window_has_focus();
if (probe_right) x = min(x + 2, 580);
window_set_caption("GQK_GM_PROBE_20261119 step=" + string(probe_step) + " x=" + string(x) + " right=" + string(probe_right) + " focus=" + string(probe_focus));
