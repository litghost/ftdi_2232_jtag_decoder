class JtagChain(object):
    """ Models a chain of JTAG models. """
    def __init__(self, models):
        assert len(models) > 0
        self.models = models

    def shift_dr(self, tdi):
        for model in self.models:
            tdo = model.shift_dr(tdi)
            # Chain to next part.
            tdi = tdo

        return tdo

    def shift_ir(self, tdi):
        for model in self.models:
            tdo = model.shift_ir(tdi)
            # Chain to next part.
            tdi = tdo

        return tdo

    def update_dr(self):
        """ DR update state has been entered. """
        for model in self.models:
            model.update_dr()

    def update_ir(self):
        """ DR update state has been entered. """
        for model in self.models:
            model.update_ir()

    def capture_dr(self):
        """ DR update state has been entered. """
        for model in self.models:
            model.capture_dr()

    def capture_ir(self):
        """ DR update state has been entered. """
        for model in self.models:
            model.capture_ir()

    def reset(self):
        """ Reset state has been entered. """
        for model in self.models:
            model.reset()

    def run_idle(self):
        """ Run-test/idle state has been entered. """
        for model in self.models:
            model.run_idle()

