import yaml


TIMES = [
        'morning',
        'afternoon',
        'night'
    ]

def hello_world():
    return "Hello World!"


def salute():
    return [ f'Good {x}!' for x in TIMES ]

def print_salutes():
    print(yaml.dump(salute(), indent=4))
