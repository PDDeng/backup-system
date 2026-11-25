"""Pure retention selection; no filesystem mutation or cleanup execution."""
def keep_cycles(config,target):
    value=target.get('keep_days',config.get('keep_days',15))
    if type(value) is not int or not 1<=value<=3650:
        raise ValueError('Retention must be 1..3650 complete date cycles')
    return value
