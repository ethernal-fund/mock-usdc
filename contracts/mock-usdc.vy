# SPDX-License-Identifier: Apache-2.0
# @version ^0.4.3

event Transfer:
    sender: indexed(address)
    receiver: indexed(address)
    value: uint256

event Approval:
    owner: indexed(address)
    spender: indexed(address)
    value: uint256

owner: public(address)
decimals: public(uint8)
name: public(String[32])
symbol: public(String[10])
totalSupply: public(uint256)
balanceOf: public(HashMap[address, uint256])
allowance: public(HashMap[address, HashMap[address, uint256]])
MAX_FAUCET_AMOUNT: constant(uint256) = 10000 * 10 ** 6 
INITIAL_SUPPLY: constant(uint256) = 100000000 * 10 ** 6 

@deploy
def __init__(_name: String[32], _symbol: String[10], _decimals: uint8):
    self.owner = msg.sender
    self.name = _name
    self.symbol = _symbol
    self.decimals = _decimals
    self.balanceOf[msg.sender] = INITIAL_SUPPLY
    self.totalSupply = INITIAL_SUPPLY
    log Transfer(sender=empty(address), receiver=msg.sender, value=INITIAL_SUPPLY)

@external
def transfer(_to: address, _value: uint256) -> bool:
    assert _to != empty(address), "Transfer to zero address"
    assert self.balanceOf[msg.sender] >= _value, "Insufficient balance"
    self.balanceOf[msg.sender] -= _value
    self.balanceOf[_to] += _value
    log Transfer(sender=msg.sender, receiver=_to, value=_value)
    return True

@external
def transferFrom(_from: address, _to: address, _value: uint256) -> bool:
    assert _to != empty(address), "Transfer to zero address"
    assert _from != empty(address), "Transfer from zero address"
    assert self.balanceOf[_from] >= _value, "Insufficient balance"
    assert self.allowance[_from][msg.sender] >= _value, "Insufficient allowance"
    self.balanceOf[_from] -= _value
    self.balanceOf[_to] += _value
    self.allowance[_from][msg.sender] -= _value
    log Transfer(sender=_from, receiver=_to, value=_value)
    return True

@external
def approve(_spender: address, _value: uint256) -> bool:
    assert _spender != empty(address), "Approve to zero address"
    self.allowance[msg.sender][_spender] = _value
    log Approval(owner=msg.sender, spender=_spender, value=_value)
    return True

@external
def faucet(_to: address, _amount: uint256):
    assert _to != empty(address), "Mint to zero address"
    assert _amount <= MAX_FAUCET_AMOUNT, "Amount exceeds faucet limit"
    self.balanceOf[_to] += _amount
    self.totalSupply += _amount
    log Transfer(sender=empty(address), receiver=_to, value=_amount)

@external
def mint(_to: address, _amount: uint256):
    assert msg.sender == self.owner, "Only owner can mint"
    assert _to != empty(address), "Mint to zero address"
    self.balanceOf[_to] += _amount
    self.totalSupply += _amount
    log Transfer(sender=empty(address), receiver=_to, value=_amount)

@external
def distribute(_recipients: DynArray[address, 50], _amounts: DynArray[uint256, 50]):
    assert msg.sender == self.owner, "Only owner can distribute"
    assert len(_recipients) == len(_amounts), "Array length mismatch"
    
    for i: uint256 in range(50):
        if i >= len(_recipients):
            break
        
        recipient: address = _recipients[i]
        amount: uint256 = _amounts[i]
        
        if recipient != empty(address):
            self.balanceOf[recipient] += amount
            self.totalSupply += amount
            log Transfer(sender=empty(address), receiver=recipient, value=amount)

@external
def changeOwner(_newOwner: address):
    assert msg.sender == self.owner, "Only owner can change owner"
    assert _newOwner != empty(address), "New owner cannot be zero address"
    self.owner = _newOwner

@external
def burn(_amount: uint256):
    assert self.balanceOf[msg.sender] >= _amount, "Insufficient balance"
    self.balanceOf[msg.sender] -= _amount
    self.totalSupply -= _amount
    log Transfer(sender=msg.sender, receiver=empty(address), value=_amount)

@external
def burnFrom(_from: address, _amount: uint256):
    assert msg.sender == self.owner, "Only owner can burn from"
    assert self.balanceOf[_from] >= _amount, "Insufficient balance"
    self.balanceOf[_from] -= _amount
    self.totalSupply -= _amount
    log Transfer(sender=_from, receiver=empty(address), value=_amount)

@view
@external
def getFaucetMax() -> uint256:
    return MAX_FAUCET_AMOUNT

@view
@external
def getOwner() -> address:
    return self.owner

@view
@external 
def getTokenInfo() -> (String[32], String[10], uint8, uint256):
    return (self.name, self.symbol, self.decimals, self.totalSupply)

@view
@external
def supportsInterface(_interface_id: bytes4) -> bool:
    return _interface_id == 0x36372b07 or _interface_id == 0x01ffc9a7