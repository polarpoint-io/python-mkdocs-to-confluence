
from src.main import hello_world, salute

def test_hello_world(): 
    assert hello_world() == "Hello World!"

def test_salute_method(): 
    assert len(salute()) == 3
