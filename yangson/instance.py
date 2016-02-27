"""Classes related to JSON-encoded instance data."""

from typing import Any, Callable, List, Tuple
from .exception import YangsonException
from .typealiases import *

Trace = List["Crumb"]
Value = Any
Object = Dict[QName, Value]
Array = List[Value]

class Crumb(object):
    """Abstract class of crumb object representing a zipper context."""
    pass

class MemberCrumb(Crumb):
    """Zipper context for an object member."""

    def __init__(self, name: QName, obj: Object) -> None:
        """Initialize the class instance.

        :param name: name of an object member that's the current focus
        :param obj: an object containing the remaining members
        """
        self.name = name
        self.object = obj

    def pointer_fragment(self) -> QName:
        """Return the JSON pointer fragment of the focused value."""
        return self.name

    def zip(self, value: Value) -> Object:
        """Put focused value back to a copy of the object and return it.

        :param value: value of the focused member
        """
        res = self.object.copy()
        res[self.name] = value
        return res

class EntryCrumb(Crumb):
    """Zipper context for an array entry."""

    def __init__(self, before: Array, after: Array) -> None:
        """Initialize the class instance.

        :param before: array entries before the focused entry
        :param after: array entries after the focused entry
        """
        self.before = before
        self.after = after

    def pointer_fragment(self) -> int:
        """Return the JSON pointer fragment of the focused value."""
        return len(self.before)

    def zip(self, value: Value) -> Array:
        """Concatenate the receiver's parts with the focused entry.

        :param value: value of the focused entry
        """
        return self.before + [value] + self.after

class Instance(object):
    """YANG data node instance implemented as a zipper structure."""

    def __init__(self, value: Value, trace: Trace = []) -> None:
        """Initialize the class instance.

        :param value: instance value
        :param trace: sequence of crumbs
        """
        self.value = value
        self.trace = trace

    @property
    def namespace(self):
        """Return the receiver's namespace identifier."""
        for cr in reversed(self.trace):
            if isinstance(cr, MemberCrumb):
                p, s, loc = cr.name.partition(":")
                if s: return p

    def crumb(self) -> Crumb:
        """Return the most recent crumb in receiver's trace."""
        return self.trace[-1]

    def _replace_crumb(self, crumb: Crumb) -> Trace:
        """Return receiver's trace with a new last crumb."""
        return self.trace[:-1] + [crumb]

    def pointer(self) -> str:
        """Return JSON pointer of the receiver."""
        return "/" + "/".join([ c.pointer_fragment() for c in self.trace ])

    def goto(self, ii: "InstanceIdentifier") -> "Instance":
        """Return an instance in the receiver's subtree.

        :param ii: instance identifier (relative to the receiver)
        """
        inst = self # type: "Instance"
        for sel in ii:
            inst = sel.goto_step(inst)
        return inst

    def peek(self, ii: "InstanceIdentifier") -> Value:
        """Return a value in the receiver's subtree.

        :param ii: instance identifier (relative to the receiver)
        """
        val = self.value
        for sel in ii:
            val = sel.peek_step(val)
        return val

    def update(self, newval: Value) -> "Instance":
        """Return a copy of the receiver with a new value.

        :param newval: new value
        """
        return self.__class__(newval, self.trace)

    @property
    def up(self) -> "Instance":
        """Ascend to the parent instance."""
        try:
            return self.__class__(self.crumb().zip(self.value),
                                  self.trace[:-1])
        except IndexError:
            raise NonexistentInstance(self, "up of top") from None

    @property
    def top(self) -> "Instance":
        inst = self
        while inst.trace:
            inst = inst.up
        return inst

    def member(self, name: QName) -> "Instance":
        obj = self.value.copy()
        try:
            return self.__class__(obj.pop(name),
                                  self.trace + [MemberCrumb(name, obj)])
        except TypeError:
            raise InstanceTypeError(self, "member of non-object") from None
        except KeyError:
            raise NonexistentInstance(self, "member " + name)

    def new_member(self, name: QName, value: Value) -> "Instance":
        if name in self.value:
            raise DuplicateMember(self, name) from None
        return self.__class__(value, self.trace + [MemberCrumb(name, self.value)])

    def sibling(self, name: QName) -> "Instance":
        try:
            cr = self.crumb()
            obj = cr.object.copy()
            newval = obj.pop(name)
            obj[cr.name] = self.value
            return self.__class__(newval, self._replace_crumb(MemberCrumb(name, obj)))
        except KeyError:
            raise NonexistentInstance(self, "member " + name)
        except IndexError:
            raise InstanceTypeError(self, "sibling of non-member")

    def entry(self, index: int) -> "Instance":
        val = self.value
        if not isinstance(val, list):
            raise InstanceTypeError(self, "entry of non-array") from None
        try:
            return self.__class__(val[index], self.trace +
                                  [EntryCrumb(val[:index], val[index+1:])])
        except IndexError:
            raise NonexistentInstance(self, "entry " + str(index)) from None

    @property
    def first_entry(self):
        val = self.value
        if not isinstance(val, list):
            raise InstanceTypeError(self, "first entry of non-array") from None
        try:
            return self.__class__(val[0], self.trace + [EntryCrumb([], val[1:])])
        except IndexError:
            raise NonexistentInstance(self, "first of empty") from None

    @property
    def last_entry(self):
        val = self.value
        if not isinstance(val, list):
            raise InstanceTypeError(self, "last entry of non-array") from None
        try:
            return self.__class__(val[-1], self.trace + [EntryCrumb(val[:-1], [])])
        except IndexError:
            raise NonexistentInstance(self, "last of empty") from None

    def look_up(self, keys: Dict[QName, Value]) -> "Instance":
        """Return the entry with matching keys."""
        if not isinstance(self.value, list):
            raise InstanceTypeError(self, "lookup on non-list")
        try:
            for i in range(len(self.value)):
                en = self.value[i]
                flag = True
                for k in keys:
                    if en[k] != keys[k]:
                        flag = False
                        break
                if flag: return self.entry(i)
            raise NonexistentInstance(self, "entry lookup failed")
        except KeyError:
            raise NonexistentInstance(self, "entry lookup failed") from None
        except TypeError:
            raise InstanceTypeError(self, "lookup on non-list") from None

    @property
    def next(self) -> "Instance":
        try:
            cr = self.crumb()
            return self.__class__(
                cr.after[0],
                self._replace_crumb(
                    EntryCrumb(cr.before + [self.value], cr.after[1:])))
        except IndexError:
            raise NonexistentInstance(self, "next of last") from None
        except AttributeError:
            raise InstanceTypeError(self, "next of non-entry") from None

    @property
    def previous(self) -> "Instance":
        try:
            cr = self.crumb()
            return self.__class__(
                cr.before[-1],
                self._replace_crumb(
                    EntryCrumb(cr.before[:-1], [self.value] + cr.after)))
        except IndexError:
            raise NonexistentInstance(self, "previous of first") from None
        except AttributeError:
            raise InstanceTypeError(self, "previous of non-entry") from None

    def insert_before(self, value: Value):
        try:
            cr = self.crumb()
            return self.__class__(value, self._replace_crumb(
                EntryCrumb(cr.before, [self.value] + cr.after)))
        except (AttributeError, IndexError):
            raise InstanceTypeError(self, "insert before non-entry") from None

    def insert_after(self, value: Value):
        try:
            cr = self.crumb()
            return self.__class__(value, self._replace_crumb(
                EntryCrumb(cr.before + [self.value], cr.after)))
        except (AttributeError, IndexError):
            raise InstanceTypeError(self, "insert after non-entry") from None

class InstanceIdentifier(list):
    """Instance identifiers."""

    def __str__(self):
        """Return a string representation of the receiver."""
        return "".join([ str(i) for i in self ])

class InstanceSelector(object):
    """Components of instance identifers."""
    pass

class MemberName(InstanceSelector):
    """Selectors of object members."""

    def __init__(self, name: QName) -> None:
        """Initialize the class instance.

        :param name: member name
        """
        self.name = name

    def __str__(self) -> str:
        """Return a string representation of the receiver."""
        return "/" + self.name

    def peek_step(self, obj: Object) -> Value:
        """Return the member of `obj` addressed by the receiver.

        :param obj: current object
        """
        return obj.get(self.name)

    def goto_step(self, inst: Instance) -> Instance:
        """Return member instance of `inst` addressed by the receiver.

        :param inst: current instance
        """
        return inst.member(self.name)

class EntryIndex(InstanceSelector):
    """Numeric selectors for a list or leaf-list entry."""

    def __init__(self, index: int) -> None:
        """Initialize the class instance.

        :param index: index of an entry
        """
        self.index = index

    def __str__(self) -> str:
        """Return a string representation of the receiver."""
        return "[{0:d}]".format(self.index)

    def peek_step(self, arr: Array) -> Value:
        """Return the entry of `arr` addressed by the receiver.

        :param arr: current array
        """
        try:
            return arr[self.index]
        except IndexError:
            return None

    def goto_step(self, inst: Instance) -> Instance:
        """Return member instance of `inst` addressed by the receiver.

        :param inst: current instance
        """
        return inst.entry(self.index)

class EntryValue(InstanceSelector):
    """Value-based selectors of an array entry."""

    def __init__(self, value: Value) -> None:
        """Initialize the class instance.

        :param value: value of a leaf-list entry
        """
        self.value = value

    def __str__(self) -> str:
        """Return a string representation of the receiver."""
        return "[.=" + str(self.value) +"]"

    def peek_step(self, arr: Array) -> Value:
        """Return the entry of `arr` addressed by the receiver.

        :param arr: current array
        """
        try:
            return arr[arr.index(self.value)]
        except ValueError:
            return None

    def goto_step(self, inst: Instance) -> Instance:
        """Return member instance of `inst` addressed by the receiver.

        :param inst: current instance
        """
        try:
            return inst.entry(inst.value.index(self.value))
        except ValueError:
            raise NonexistentInstance(inst,
                                       "entry '{}'".format(str(self.value)))

class EntryKeys(InstanceSelector):
    """Key-based selectors for a list entry."""

    def __init__(self, keys: Dict[QName, Value]) -> None:
        """Initialize the class instance.

        :param keys: dictionary with keys of an entry
        """
        self.keys = keys

    def __str__(self) -> str:
        """Return a string representation of the receiver."""
        return "".join(["[{}={}]".format(k, repr(self.keys[k]))
                        for k in self.keys])

    def peek_step(self, arr: Array) -> Value:
        """Return the entry of `arr` addressed by the receiver.

        :param arr: current array
        """
        for en in arr:
            flag = True
            for k in self.keys:
                if en[k] != self.keys[k]:
                    flag = False
                    break
            if flag: return en

    def goto_step(self, inst: Instance) -> Instance:
        """Return member instance of `inst` addressed by the receiver.

        :param inst: current instance
        """
        return inst.look_up(self.keys)

# Exceptions

class InstanceError(YangsonException):
    """Exceptions related to operations on the instance structure."""

    def __init__(self, inst: Instance):
        self.instance = inst

    def __str__(self):
        return "[" + self.instance.pointer() + "] "

class NonexistentInstance(InstanceError):
    """Exception to raise when moving out of bounds."""

    def __init__(self, inst: Instance, text: str) -> None:
        super().__init__(inst)
        self.text = text

    def __str__(self):
        return "{} {}".format(super().__str__(), self.text)

class InstanceTypeError(InstanceError):
    """Exception to raise when calling a method for a wrong instance type."""

    def __init__(self, inst: Instance, text: str) -> None:
        super().__init__(inst)
        self.text = text

    def __str__(self):
        return "{} {}".format(super().__str__(), self.text)

class DuplicateMember(InstanceError):
    """Exception to raise on attempt to create a member that already exists."""

    def __init__(self, inst: Instance, name: QName) -> None:
        super().__init__(inst)
        self.name = name

    def __str__(self):
        return "{} member {}".format(super().__str__(), self.name)