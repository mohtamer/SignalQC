"""Bellagamba wrapper with the original feature transform and frozen weights.

Unused reads of data/data_test.csv and creation of Results were removed.
"""
import GMClassifierFx as gmc


def bella(data):
    transformed = gmc.preprocessData([data])
    model = gmc.neuralNet()
    model.loadNN("GMClassifier_CantWell")
    return model.useNN(transformed[0])[0].tolist()
