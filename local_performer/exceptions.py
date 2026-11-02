"""
================================================================================
    Excepciones del Performer (Modelo REFramework)
================================================================================
Diferencia formalmente entre errores funcionales esperados y fallos técnicos.
"""


class BusinessException(Exception):
    """
    Excepción de Negocio: Fallo funcional esperado en los datos o reglas de negocio
    (ej: registro no encontrado, importe negativo, formato incorrecto).
    -> NO debe reintentarse (se marca como 'Business' en la cola).
    """
    def __init__(self, code: str = "SL-02", message: str = "Error funcional de negocio", details: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


class SystemException(Exception):
    """
    Excepción de Sistema: Error técnico no controlado o de infraestructura
    (ej: caída de red, timeout de selector, página no disponible).
    -> Puede reintentarse automáticamente si quedan reintentos configurados.
    """
    def __init__(self, message: str = "Error técnico del sistema", details: str = ""):
        super().__init__(message)
        self.message = message
        self.details = details

