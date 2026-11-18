define config.save_directory = "gameqa-renpy-fixture"
init python:
    import json
    import os
    import pygame_sdl2 as pygame
    import time
    config.name = "GameQA RenPy Fixture"
    config.screen_width = 320
    config.screen_height = 240
    config.autosave_on_quit = False
    config.autosave_frequency = None
    config.developer = False
    fixture_x = 60
    fixture_held = False
    fixture_confirms = 0
    fixture_cancels = 0
    fixture_events = []
    fixture_mp = MultiPersistent("gameqa-renpy-fixture", save_on_quit=False)
    class Fixture(renpy.Displayable):
        def render(self, width, height, st, at):
            result = renpy.Render(320, 240)
            blank = os.path.exists(os.path.join(os.environ["GAMEQA_FIXTURE_ROOT"], "blank"))
            result.canvas().rect("#405060" if os.path.exists(os.path.join(os.environ["GAMEQA_FIXTURE_ROOT"], "background")) else "#000000" if blank else "#203040", (0, 0, 320, 240))
            result.canvas().rect("#000000" if blank else "#ff8020", (fixture_x, 100, 32, 32))
            if os.path.exists(os.path.join(os.environ["GAMEQA_FIXTURE_ROOT"], "speck")):
                result.canvas().rect("#ffffff", (2, 2, 1, 1))
            renpy.redraw(self, .05)
            return result
        def event(self, ev, x, y, st):
            global fixture_x, fixture_held, fixture_confirms, fixture_cancels
            if ev.type in (pygame.KEYDOWN, pygame.KEYUP):
                if ev.key == pygame.K_RIGHT:
                    fixture_held = ev.type == pygame.KEYDOWN
                    if fixture_held:
                        fixture_x += 80
                if ev.type == pygame.KEYDOWN and ev.key == pygame.K_RETURN:
                    fixture_confirms += 1
                    if os.path.exists(os.path.join(os.environ["GAMEQA_FIXTURE_ROOT"], "blank-after-confirm")):
                        open(os.path.join(os.environ["GAMEQA_FIXTURE_ROOT"], "blank"), "w").close()
                    if os.path.exists(os.path.join(os.environ["GAMEQA_FIXTURE_ROOT"], "stall")):
                        time.sleep(3)
                    if os.path.exists(os.path.join(os.environ["GAMEQA_FIXTURE_ROOT"], "save")):
                        renpy.save("1-1", "GameQA fixture")
                        persistent.gameqa_fixture = "isolated"
                        renpy.save_persistent()
                        fixture_mp.marker = "isolated"
                        fixture_mp.save()
                        with open(os.path.join(os.environ["GAMEQA_FIXTURE_ROOT"], "paths.json"), "w") as stream:
                            json.dump(dict(locations=[item.directory for item in renpy.loadsave.location.locations],
                                savedir=config.savedir, multipersistent=fixture_mp._filename,
                                tokens=renpy.savetoken.token_dir), stream)
                if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                    fixture_cancels += 1
                    if os.path.exists(os.path.join(os.environ["GAMEQA_FIXTURE_ROOT"], "load")):
                        renpy.load("1-1")
                if ev.type == pygame.KEYDOWN and os.path.exists(os.path.join(os.environ["GAMEQA_FIXTURE_ROOT"], "drop-draw")):
                    renpy.test.testexecution.execute = lambda: None
                fixture_events.append(dict(type="down" if ev.type == pygame.KEYDOWN else "up", key=ev.key))
                renpy.redraw(self, 0)
            with open(os.path.join(os.environ["GAMEQA_FIXTURE_ROOT"], "fixture.json"), "w") as stream:
                json.dump(dict(x=fixture_x, held=fixture_held, confirms=fixture_confirms,
                    cancels=fixture_cancels, events=fixture_events), stream)
            return None
    fixture_displayable = Fixture()
screen fixture():
    add fixture_displayable
label main_menu:
    jump start
label start:
    call screen fixture
