class InjectorError(RuntimeError):
    pass

class FormatError(InjectorError):
    pass

class StormLibError(InjectorError):
    pass

class ValidationError(InjectorError):
    pass
