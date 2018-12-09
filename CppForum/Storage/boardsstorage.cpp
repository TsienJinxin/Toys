#include "boardsstorage.h"
#include <QDebug>

BoardsStorage::BoardsStorage()
{

}

ForumStorage& BoardsStorage::operator <<(QVector<QString>& record) {
    if(record.size() != 2){
        qDebug()<<"A board record must have 2 fields";
        return ForumStorage::GetNullValue();
    }

    query.prepare("insert into boards values (?,?)");
    query.addBindValue(record[0]);
    query.addBindValue(record[1]);

    bool success = query.exec();
    if(success) {
        qDebug()<<"insert into boards table successfully";
    } else {
        qDebug()<<"inserting into boards table failed";
        return ForumStorage::GetNullValue();
    }

    return *this;
}

ForumStorage& BoardsStorage::operator >>(QVector<QString>& record) {
    if(!dataAvailable) {
        dataAvailable = query.exec("select name, moderator from boards");
        query.next();
    }

    if(query.isValid()) {
        dataAvailable = true;
        for(int i=0; i<2; i++)
            record.push_back(query.value(i).toString());
        query.next();  // so that we can see if there is remaining record in the
                       // result of 'select statement' (via query.isValid())
    } else {
        dataAvailable = false;
    }

    return *this;
}
