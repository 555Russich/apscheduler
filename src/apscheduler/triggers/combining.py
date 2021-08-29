from abc import abstractmethod
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Union

from ..abc import Trigger
from ..exceptions import MaxIterationsReached
from ..marshalling import marshal_object, unmarshal_object
from ..validators import as_list, as_positive_integer, as_timedelta, require_state_version


class BaseCombiningTrigger(Trigger):
    __slots__ = 'triggers', '_next_fire_times'

    def __init__(self, triggers: Sequence[Trigger]):
        self.triggers = as_list(triggers, Trigger, 'triggers')
        self._next_fire_times: List[Optional[datetime]] = []

    def __getstate__(self) -> Dict[str, Any]:
        return {
            'version': 1,
            'triggers': [marshal_object(trigger) for trigger in self.triggers],
            'next_fire_times': self._next_fire_times
        }

    @abstractmethod
    def __setstate__(self, state: Dict[str, Any]) -> None:
        self.triggers = [unmarshal_object(*trigger_state) for trigger_state in state['triggers']]
        self._next_fire_times = state['next_fire_times']


class AndTrigger(BaseCombiningTrigger):
    """
    Fires on times produced by the enclosed triggers whenever the fire times are within the given
    threshold.

    If the produced fire times are not within the given threshold of each other, the trigger(s)
    that produced the earliest fire time will be asked for their next fire time and the iteration
    is restarted. If instead all of the triggers agree on a fire time, all the triggers are asked
    for their next fire times and the earliest of the previously produced fire times will be
    returned.

    This trigger will be finished when any of the enclosed trigger has finished.

    :param triggers: triggers to combine
    :param threshold: maximum time difference between the next fire times of the triggers in order
        for the earliest of them to be returned from :meth:`next` (in seconds, or as timedelta)
    :param max_iterations: maximum number of iterations of fire time calculations before giving up
    """

    __slots__ = 'threshold', 'max_iterations'

    def __init__(self, triggers: Sequence[Trigger], *, threshold: Union[float, timedelta] = 1,
                 max_iterations: Optional[int] = 10000):
        super().__init__(triggers)
        self.threshold = as_timedelta(threshold, 'threshold')
        self.max_iterations = as_positive_integer(max_iterations, 'max_iterations')

    def next(self) -> Optional[datetime]:
        if not self._next_fire_times:
            # Fill out the fire times on the first run
            self._next_fire_times = [t.next() for t in self.triggers]

        for _ in range(self.max_iterations):
            # Find the earliest and latest fire times
            earliest_fire_time: Optional[datetime] = None
            latest_fire_time: Optional[datetime] = None
            for fire_time in self._next_fire_times:
                # If any of the fire times is None, this trigger is finished
                if fire_time is None:
                    return None

                if earliest_fire_time is None or earliest_fire_time > fire_time:
                    earliest_fire_time = fire_time

                if latest_fire_time is None or latest_fire_time < fire_time:
                    latest_fire_time = fire_time

            # Replace all the fire times that were within the threshold
            for i, trigger in enumerate(self.triggers):
                if self._next_fire_times[i] - earliest_fire_time <= self.threshold:
                    self._next_fire_times[i] = self.triggers[i].next()

            # If all the fire times were within the threshold, return the earliest one
            if latest_fire_time - earliest_fire_time <= self.threshold:
                self._next_fire_times = [t.next() for t in self.triggers]
                return earliest_fire_time
        else:
            raise MaxIterationsReached

    def __getstate__(self) -> Dict[str, Any]:
        state = super().__getstate__()
        state['threshold'] = self.threshold.total_seconds()
        state['max_iterations'] = self.max_iterations
        return state

    def __setstate__(self, state: Dict[str, Any]) -> None:
        require_state_version(self, state, 1)
        super().__setstate__(state)
        self.threshold = timedelta(seconds=state['threshold'])
        self.max_iterations = state['max_iterations']

    def __repr__(self):
        return f'{self.__class__.__name__}({self.triggers}, ' \
               f'threshold={self.threshold.total_seconds()}, max_iterations={self.max_iterations})'


class OrTrigger(BaseCombiningTrigger):
    """
    Fires on every fire time of every trigger in chronological order.
    If two or more triggers produce the same fire time, it will only be used once.

    This trigger will be finished when none of the enclosed triggers can produce any new fire
    times.

    :param triggers: triggers to combine
    """

    __slots__ = ()

    def next(self) -> Optional[datetime]:
        # Fill out the fire times on the first run
        if not self._next_fire_times:
            self._next_fire_times = [t.next() for t in self.triggers]

        # Find out the earliest of the fire times
        earliest_time: Optional[datetime] = min([fire_time for fire_time in self._next_fire_times
                                                 if fire_time is not None], default=None)
        if earliest_time is not None:
            # Generate new fire times for the trigger(s) that generated the earliest fire time
            for i, fire_time in enumerate(self._next_fire_times):
                if fire_time == earliest_time:
                    self._next_fire_times[i] = self.triggers[i].next()

        return earliest_time

    def __setstate__(self, state: Dict[str, Any]) -> None:
        require_state_version(self, state, 1)
        super().__setstate__(state)

    def __repr__(self):
        return f'{self.__class__.__name__}({self.triggers})'
